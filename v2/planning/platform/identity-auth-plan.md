# Identity & Auth — standardising sign-in (JWT access + rotating refresh)

Status: **P1 BUILT** (uncommitted, 2026-08-15). P2–P5 planned.

| Phase | State |
|---|---|
| P0 spec | DONE — this document |
| **P1 identity module + dual credentials** | **DONE** — `v2/identity/`, `/auth/*`, credential dispatch in `/resolve`, `identities` backfill, discovery endpoint, issuer wired in Terraform + compose. 22 tests in `tests/unit/test_identity_auth.py`; full unit suite green (1810 passed). |
| **§6 one baked URL + discovery** | **DONE** — `/.well-known/agentd-platform`, `AGENTD_AUTH_ISSUER` per stack, `platform_url` in the profile/flavors/terraform/sync-script, daemon-side resolver with disk cache (`infrastructure/platform_discovery.py`), renderer-side `lib/discovery.ts`. 13 tests in `tests/unit/test_platform_discovery.py`; all three client workspaces typecheck; web build green. |
| **P2 verifiers off the hot path** | **DONE** — shared `identity/infrastructure/jwks_verifier.py` (one implementation, shipped in both images); daemon `accounts.resolve` and proxy `custom_auth` verify locally, falling back to `/resolve` for `sess_`. 12 tests. |
| **P3 client refresh loop** | **DONE** — `lib/tokens.ts` (single-flight rotation, 80%-of-life renewal), `auth.update` RPC + expiry guard with grace window, desktop `safeStorage` for the refresh token, `restoreSession()` at boot. 7 tests. |
| **P4 browser/PKCE + providers** | **DONE** — `oidc_provider.py` (one adapter for Google/Microsoft/Cognito), `oauth_flow.py` (PKCE, single-use state), `/auth/authorize` + `/auth/callback`, providers advertised through discovery as data. 16 tests. |
| **P5 cut over** | **DONE** — no installer exists outside the team, so there was nothing to keep compatibility with. `sess_` issuance and resolution deleted, the `sessions` table dropped from new schemas (existing DBs keep it, unread — an irreversible DDL step for a few kilobytes is a bad trade), `ACCOUNTS_SESSION_TTL_DAYS` removed. `/login` is now a thin alias of `/auth/login`. |

Nothing is committed — the working tree holds it all.

## Why

Today sign-in is: `POST /login` → an opaque `sess_<random>` row in a SQLite `sessions` table, sent
as a bearer token, resolved by a network round trip on **every uncached model call**
(`model_proxy/custom_auth.py`) and on every socket connect (`agent_runtime/infrastructure/accounts.py`).

That works, and it is deliberately simple. Four things are wrong with it going forward:

1. **Accounts is a hard dependency in the hot path of every model call.** `custom_auth.py` already
   carries a `ACCOUNTS_RESOLVE_GRACE_S` stale-serving hack to survive blips. That is a symptom.
2. **No refresh story.** A 30-day opaque token is the credential. Revocation is all-or-nothing and
   the token is long-lived enough that leaking it matters.
3. **Identity is welded to email+password.** There is no seam where Google / Microsoft / Cognito
   could plug in. `accounts.email` is effectively the primary key of a human.
4. **"Same account everywhere" is not actually true today.** The desktop flavors and the web build
   each bake their own accounts URL, and they have drifted to three different ELB hostnames
   (`agentd-dev-91895423`, `agentd-dev-1074034559`, `agentd-dev-1192385083`). Different stack =
   different `accounts.db` = the same email is a *different* `acct_` id with different credits.

## The three requirements, restated as invariants

- **R1 Non-destructive.** No account is deleted, no credit balance moves, no state directory is
  renamed. Everything downstream of identity keys on `account_id`, and `account_id` does not change.
- **R2 One account everywhere.** Desktop (local or cloud mode) and web resolve to the same
  `acct_` id, the same credits, the same usage ledger, the same entitlements.
- **R3 Replaceable.** Swapping the local password store for Cognito, or adding Google/Microsoft,
  must be an adapter + a config value — not a change to agentd, the model proxy, or the clients.

---

## 1. The single most important decision

**`sub` in the JWT is the existing `acct_<hex>` id.**

Every table (`usage`, `credit_grants`, `entitlements`, `subscriptions`), every state path
(`<state_dir>/accounts/<acct>/agents/<agent>/`), every ownership check
(`domain/ownership.py`), and every memory partition (`accounts.memory_partition`) already keys on
that string. Keeping it as the token subject is what makes R1 free rather than a migration.

Corollary: **email stops being identity.** It becomes an attribute of an *identity record*.

---

## 2. New module: `v2/identity/`

Same shape and the same rules as `v2/payments/` — a self-contained hexagonal module that the
accounts service consumes through interfaces and one composition root. Accounts never names a
provider, never imports an adapter, and never learns what a JWT is.

```
v2/identity/
  domain/
    principal.py          Principal(account_id, email, scopes, amr, session_id)
    token.py              AccessClaims, TokenPair, RefreshToken
    errors.py             IdentityConfigurationError, AuthenticationFailed, TokenInvalid
  application/interfaces/
    identity_provider.py  authenticate() / register() / from_external_assertion()
    token_issuer.py       issue(principal) -> TokenPair ; verify(token) -> AccessClaims
    refresh_store.py      rotating refresh persistence + reuse detection
    key_store.py          signing keys, active kid, public JWKS
  application/services/
    auth_service.py       login / refresh / logout / logout_all — provider-agnostic orchestration
    principal_service.py  identity record -> account (create-on-first-login, link, merge)
  infrastructure/
    local_password_provider.py   today's PBKDF2 `accounts` rows, verbatim
    jwt_token_issuer.py          PyJWT, EdDSA default, alg-agnostic verify via JWKS `kid`
    sqlite_refresh_store.py
    sqlite_key_store.py          private keys encrypted with AGENTD_IDENTITY_KEK
    oidc_provider.py             LATER — Google / Microsoft / Cognito, via discovery doc
  main/
    identity_factory.py   AGENTD_IDENTITY_PROVIDER = local | oidc | cognito
  presentation/
    auth_router.py        the /auth/* FastAPI router, mounted by accounts/app.py
```

`identity_factory.py` follows `payments/main/payment_gateway_factory.py` exactly, including
**raising on an unknown provider name** rather than falling back — a typo in
`AGENTD_IDENTITY_PROVIDER` must be a five-minute outage, never a silently-wrong auth mode.

---

## 3. Tokens

### Access token — JWT, short-lived, verified locally

TTL **10 minutes**. Signed with **EdDSA (Ed25519)** by default: a 64-byte signature keeps the token
small enough to sit in a WebSocket query string and to be handed to LiteLLM as an `api_key`.

**The verifier is algorithm-agnostic** — it reads `kid` from the header, fetches the key from JWKS,
and uses that key's `alg`. This is not gold-plating: Cognito signs RS256, so an EdDSA-only verifier
would have to be rewritten on the day we swap. Written this way, the swap is a URL change.

```
iss    https://accounts.<env>.<domain>     issuer; a dev token is rejected by prod, loudly
sub    acct_<hex>                          THE EXISTING ACCOUNT ID (see §1)
aud    ["agentd-daemon", "agentd-proxy"]   who may accept it
exp/iat/nbf/jti
sid    the auth-session id (replaces today's `sessions` row)
scope  "chat spend"                        space-delimited, OAuth2 convention
amr    ["pwd"] | ["google"] | ["oidc"]     how they proved it
email, email_verified
ver    1
```

`aud` and `scope` are declared in P1 **even though nothing enforces them yet**. Retrofitting claims
into already-issued tokens is the expensive part; issuing them from day one costs nothing. They are
what later lets an agent-app connection get a downscoped token (`scope: "chat"`, `agent: <id>`)
instead of the full-power one — the auth half of the app-platform's scoped connections.

### Refresh token — opaque, rotating, reuse-detecting

`rt_<32 bytes urlsafe>`. Stored **hashed (sha256)**, never in plaintext, in:

```sql
CREATE TABLE refresh_tokens (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  token_hash   TEXT UNIQUE NOT NULL,
  account_id   TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  family_id    TEXT NOT NULL,          -- one login = one family
  parent_id    INTEGER,                -- the token this one rotated from
  client_id    TEXT NOT NULL DEFAULT '',   -- 'web' | 'desktop' | 'cli'
  device_label TEXT NOT NULL DEFAULT '',   -- for a "your devices" screen
  issued_at    REAL NOT NULL,
  expires_at   REAL NOT NULL,          -- 30d sliding
  family_expires_at REAL NOT NULL,     -- 90d absolute — a family cannot live forever
  used_at      REAL NOT NULL DEFAULT 0,
  revoked_at   REAL NOT NULL DEFAULT 0
);
```

**Rotation with reuse detection.** Every `/auth/refresh` returns a NEW refresh token and marks the
old one used. Presenting an already-used token means it was stolen (or a client raced itself) →
**revoke the entire family**, force re-login, count it. This is the standard defence and it is
cheap to build now; it is very awkward to add to a design that assumed a static token.

### Why local verification is the point

Once the daemon and the proxy verify signatures against a cached JWKS, **Accounts leaves the hot
path of every model call.** The `resolve_latency_ms` on every message goes away, the stale-grace
hack in `custom_auth.py` becomes unnecessary, and an Accounts outage stops being a platform outage.

The honest trade: an access token stays valid until `exp` even if revoked — **up to 10 minutes**.
Refresh revocation is immediate, so a compromised account is contained in one TTL. If we ever need
harder revocation, add a `sid`/`jti` denylist polled from `/auth/revocations` — declared here so the
`sid` claim exists to support it, not built in P1.

---

## 4. Identity records — how Google/Microsoft/Cognito get added later

```sql
CREATE TABLE identities (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  provider     TEXT NOT NULL,          -- 'local' | 'google' | 'microsoft' | 'cognito'
  subject      TEXT NOT NULL,          -- the provider's own stable user id
  account_id   TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  email        TEXT NOT NULL DEFAULT '',
  email_verified INTEGER NOT NULL DEFAULT 0,
  linked_at    REAL NOT NULL
);
CREATE UNIQUE INDEX ix_identities_provider_subject ON identities(provider, subject);
CREATE INDEX ix_identities_account ON identities(account_id);
```

Backfill at P1: one `('local', <account_id>, <account_id>)` row per existing account. Nothing else
changes; the local provider keeps reading `accounts.pw_salt/pw_hash` exactly as it does now.

Adding Google then means: one `identities` row, one adapter, one button. **Linking** an existing
account = a second row pointing at the same `account_id`, which is why the account is not the
identity. Auto-linking by matching email is **deliberately refused** unless the provider asserts
`email_verified` — otherwise an unverified-email provider is an account takeover.

---

## 5. Endpoints

All on the accounts service, mounted from `identity/presentation/auth_router.py`.

| Endpoint | Purpose |
|---|---|
| `POST /auth/login` | `{email,password,client_id,device_label}` → `{access_token, refresh_token, expires_in, token_type, account_id, email}` |
| `POST /auth/refresh` | `{refresh_token}` → a new pair (rotated) |
| `POST /auth/logout` | revoke this refresh family |
| `POST /auth/logout-all` | revoke every family for the account |
| `GET  /auth/sessions` | this account's live devices (from `refresh_tokens`) |
| `GET  /auth/jwks.json` | public keys, all non-expired, keyed by `kid` |
| `GET  /.well-known/agentd-platform` | **discovery** — see §6 |
| `POST /auth/authorize`, `POST /auth/token` | P4: OAuth2 code + PKCE |

Kept exactly as-is, response shape unchanged: `/signup`, `/resolve`, `/budget/{id}`, `/funding`,
`/me/credits`, `/me/purchase`, `/debit`, `/usage`, `/grant`, `/products`.

`/resolve` gains **credential dispatch** and nothing else:

```python
def resolve_credential(token: str) -> Principal:
    if token.startswith("sess_"):        # legacy — DB lookup, exactly today's code path
        return _legacy_session(token)
    return token_issuer.verify(token)    # JWT
```

That one function is the whole compatibility story. agentd, the model proxy, `ingest/app.py`, and
`infrastructure/publish/authenticator.py` all call `/resolve` and all keep working untouched.

---

## 6. Discovery — the fix for R2

The three-different-ELB drift happens because every client bakes every URL. Replace that with **one
baked value and a discovery fetch**:

```
GET https://<platform_url>/.well-known/agentd-platform
{
  "issuer":          "https://accounts.dev.agentd.io",
  "auth_url":        "https://accounts.dev.agentd.io",
  "jwks_uri":        "https://accounts.dev.agentd.io/auth/jwks.json",
  "ws_url":          "wss://app.dev.agentd.io",
  "model_proxy_url": "https://models.dev.agentd.io",
  "providers":       [{"id":"local","label":"Email"}],
  "min_client":      "0.1.1"
}
```

- Desktop `distribution.toml` `[platform]` keeps **one** key: `platform_url`. `accounts_url` /
  `model_proxy_url` become read-only fallbacks for already-shipped installs.
- The web build bakes the same single value.
- Terraform emits `platform_url` once per stack (`infra/modules/outputs.tf`), and
  `sync-platform-urls.mjs` writes that one key into every flavor.
- **`iss` is checked** by the daemon and the proxy. A token from the dev stack presented to prod is
  a typed 401 with the issuer named in the message — the "same email, two different accounts"
  confusion becomes a visible error instead of silent divergence.

This item is not optional and not deferrable: without it, R2 is still false after all the JWT work.

---

## 7. The WebSocket problem (the one genuinely new engineering issue)

A short-lived access token collides with a long-lived socket and with agent runs that outlive
10 minutes. Naming the problem and its answer here because it is where this design can go wrong.

**Connect.** The credential still rides the connect URL: `?access_token=` (with `?session=` kept as
an alias so nothing breaks). The daemon verifies it locally and pins a `Principal` on the
connection, exactly where `accounts.resolve` pins the account dict today
(`gateway.py:_handle_conn`). Browsers cannot set headers on a WebSocket, so the query string stays;
the 10-minute TTL is what bounds the blast radius of it appearing in an access log.

**Mid-socket expiry.** Two things depend on the pinned credential: the connection's identity, and
`model_proxy.turn_key()` — the string handed to LiteLLM as the API key.

- New RPC **`auth.update {access_token}`**. The client refreshes ~60s before expiry and pushes the
  new token; the daemon swaps the connection's credential in place. No reconnect, no dropped run.
- New event **`auth.expiring`** so a client that missed its own timer is prodded.
- If the credential does expire: the daemon **refuses to start a new turn** with a typed
  `auth_expired` error (the client refreshes and retries automatically), and gives **in-flight runs
  a bounded grace window** (`AGENTD_AUTH_GRACE_S`, default 120s) so a long agent run is not killed
  mid-tool-call. Same trade the existing stale-grace makes, but bounded and typed.

**Explicitly rejected:** having the daemon hold the refresh token and renew on the client's behalf.
That would make the daemon a credential store again — precisely what the current
"identity is a property of the connection, the daemon stores nothing" design removed, and what
allowed two windows on one machine to be two different people.

---

## 8. Client changes

`clients/ui/src/lib/auth.ts` splits, so the same renderer serves web and desktop with one code path:

- **`lib/auth/storage.ts`** — a `TokenStorage` interface.
  - web: refresh token in `localStorage`, access token in memory only.
  - desktop: refresh token via Electron **`safeStorage`** through a new preload bridge
    (`platform.secrets`) — encrypted at rest instead of sitting in `localStorage`.
- **`lib/auth/session.ts`** — holds the pair, schedules refresh, exposes
  `getAccessToken(): Promise<string>` with **single-flight** refresh (ten tabs waking together must
  produce one refresh, not ten — and with rotation, ten concurrent refreshes would look like token
  reuse and nuke the family).
- **`lib/auth/provider.ts`** — `signIn()` is password today, browser+PKCE at P4. Callers only ever
  see `signIn()` / `signOut()` / `getAccessToken()`.

`store.ts`'s url provider already appends `?session=`/`?mode=` on every (re)connect — it changes to
`await getAccessToken()` and the param name. `gateway.reconnect()` on sign-in stays as-is.

---

## 9. Sign-in UI

Today the entire auth surface is [`components/SignIn.tsx`](../../clients/ui/src/components/SignIn.tsx):
one 340px card with a mode toggle that flips the same form between sign-in and sign-up. It works,
and it is below the bar for a product that sells credits. What is missing is not decoration:

- **No forgot-password path at all.** Losing a password today means losing the account.
- **No client-side validation.** The 8-character minimum is a *placeholder string*; the only thing
  that enforces it is the server, so the user learns after a round trip.
- **Raw server strings as error copy.** `String(err.message)` renders
  `sign-in failed (HTTP 429)` verbatim. The rate limiter and the lockout have no human voice.
- **Sign-up and sign-in share one form.** They need different fields (confirm, strength, terms),
  different copy, and different success behaviour.
- **Accessibility gaps.** The brand is a `<div>`, so there is no `<h1>` and no heading structure;
  the error has no `role="alert"`; inputs carry no `aria-invalid`/`aria-describedby`; nothing is
  autofocused; there is no caps-lock hint on the password field.
- **The brand is hardcoded to the string `agentd`**, ignoring `flavor.productName`. Every
  white-label flavor (figure-creator, studio, game-master) currently signs in under the wrong name.
  `assets/nakama.svg` exists and is unused here.
- **Desktop has no way out.** Picking Cloud in the Launcher and then wanting Local means being
  stuck on the gate — there is no "back".

### Structure — one shell, several views

Auth becomes a small view state machine rather than a boolean, because P4 needs real destinations
(`/auth/callback`) and forgot/reset are separate screens in every product that has them:

```
<AuthShell>            layout, branding, theme toggle, provider slot, footer — written ONCE
  ├── SignInView       email + password + forgot link
  ├── SignUpView       email + password (+ strength) + confirm
  ├── ForgotView       email -> "check your inbox" (always the same answer; see below)
  ├── ResetView        new password, reached from the emailed link
  └── CallbackView     P4 — OAuth code exchange, spinner only
```

`AuthShell` is the important piece: it is where the provider buttons land at P4, so adding Google
touches one file, and it is where branding is read from `flavor` instead of hardcoded.

### Layout

- **Web, ≥900px** — split: brand panel left (product name, one line of positioning, the aurora
  gradient the chat already uses via `--aur-lime`/`--aur-blue`/`--aur-violet`), form right, max
  400px. This is the standard shape and it costs nothing new — the tokens exist.
- **Web <900px and all desktop** — the centered card, since the desktop window is narrow and a
  split panel would be cramped. Same components, one media query.
- **Desktop only** — a drag region on the top strip, and a "← Use Local instead" link back to the
  Launcher so Cloud is not a trap.

### Components and states

Every view: **idle → validating → submitting → error → success**. Concretely —

- **Inputs** reuse `.signin-input`'s existing focus treatment (`--accent` border +
  `--accent-soft` 3px ring). Add `name` attributes; password managers need them alongside the
  `autoComplete` tokens that are already right.
- **Password field** gets a reveal toggle (lucide `Eye`/`EyeOff`, 16px) and a **caps-lock hint**.
  Sign-up additionally gets a strength meter — four segments driven by length + character classes,
  labelled in text, never by colour alone.
- **Validation is client-first, server-authoritative.** Validate on blur and before submit (email
  shape, 8-char minimum, confirm match) so the common mistakes never cost a round trip; the server
  remains the truth and its errors still render.
- **Error copy is mapped, not echoed.** One table keyed on status + the server's `detail`:
  401 → "That email and password don't match."; 409 → "There's already an account with that
  email." with a link to sign in; 429 → "Too many attempts. Try again in a minute."; network →
  "Can't reach the sign-in service." Field-level errors sit under their field; form-level errors sit
  above the button.
- **Submit button** shows a spinner and keeps its label (never "Please wait…" replacing the verb),
  stays disabled while submitting, and the form ignores repeat Enter presses.
- **Forgot** always answers "If that email has an account, we've sent a link" — the same response
  whether or not the account exists, so the screen is not an account-enumeration oracle. (The
  rate limiter already applies; the email sender itself is unscheduled, see §13.)

### Provider buttons are data, never code

At P4 the buttons render from the discovery doc's `providers` array (§6) — `[{id, label, icon}]` —
above the email form with an "or" divider. **No component names Google.** Adding Microsoft is a
config entry on the server and zero client change, which is the same rule the rest of the codebase
follows for models, tools and plugins.

The slot is built in P1 and renders nothing while `providers` has only `local`.

### Accessibility (non-negotiable, per the repo's UI rules)

- Real `<h1>` per view; the card is a `<main>`; the form has an accessible name.
- Errors: `role="alert"` + `aria-live="polite"`; inputs get `aria-invalid` and
  `aria-describedby` pointing at their message.
- Focus moves to the first field on mount and to the error region on a failed submit.
- Visible focus rings on every control including the reveal toggle and the text links.
- 44px minimum touch targets; the current `.signin-toggle` at 4px padding is below it.
- Strength and validity are never communicated by colour alone — always an accompanying label.
- Respect `prefers-reduced-motion` for the aurora panel and any transition.

### Token discipline

No new colours. Surfaces `--bg/--bg2/--bg4`, text `--text/--dim`, primary `--prim-bg`/`--prim-ink`,
error `--danger`/`--danger-soft`, radius `--radius`, shadow `--shadow`, fonts `--display`/`--sans`.
The strength meter uses `--accent` at varying opacity, not a red→green ramp (that would be both a
new palette and colour-only meaning).

## 10. Phases

Each phase ships alone and is independently revertible.

**P0 — spec.** This document, the claim set, the endpoint contracts, the discovery doc shape,
issuer URLs per environment. No code.

**P1 — identity module + dual credentials.** `v2/identity/`, the new tables (additive DDL, owned by
identity and versioned with its own `schema_version` table — **not** the `PRAGMA table_info` pattern
`_init_db` uses for the money tables; see §11), `/auth/*`, `identities` backfill, `/resolve`
credential dispatch. **Nothing else in the fleet changes** — the proxy, agentd, ingest and publish
keep calling `/resolve` and keep getting the same JSON. Login can issue a legacy `sess_` row
alongside the JWT (flag `AGENTD_ISSUE_LEGACY_SESSION=1`) so an old shipped client build still works.
**Do the §6 discovery/URL-drift fix in this phase** — R2 is false until it lands.

**P2 — verifiers off the hot path.** `agent_runtime/infrastructure/accounts.py` and
`model_proxy/custom_auth.py` gain a local JWT verify path with a JWKS cache (disk-backed, `kid`-miss
refresh), falling back to `/resolve` for `sess_` tokens. Deletes a round trip from every model call.

**P3 — client refresh loop.** Token-pair storage, single-flight refresh, `auth.update` RPC,
`auth_expired` typed error + grace window, desktop `safeStorage`. Web and desktop identical.

**P4 — browser flow + external providers.** `/auth/authorize` + `/auth/token` (OAuth2 code + PKCE),
desktop opens the system browser and listens on `127.0.0.1:<random>/callback`, `oidc_provider.py`.
After this, "Sign in with Google" is a provider row + a button on **our** login page, and swapping
wholesale to Cognito is pointing `issuer`/`authorize_url` at Cognito's hosted UI — the clients
already speak the protocol.

**P5 — cut over.** Stop issuing `sess_`, drop the `sessions` table, delete the legacy branch of
`resolve_credential`.

---

## 11. Storage — SQLite now, Postgres later

**Stay on SQLite for this whole plan.** `infra/modules/variables.tf` already says so
(`AGENTD_ACCOUNTS_DB = "/data/accounts.db"` on EFS, RDS noted as a follow-up), and identity does
not change the calculation.

It is adequate for a specific reason worth stating: **once verification is JWKS-based, the database
is off the hot path entirely.** Signature checks happen in the daemon and the proxy against a
cached public key. The DB only sees login and refresh — a handful of requests per user per *day*,
not per message. Today's design puts a `/resolve` round trip in front of every model call; this one
does the opposite.

### What to do now so the swap is mechanical rather than a rewrite

1. **Make the DSN a URL, not a path.** `AGENTD_ACCOUNTS_DB=/data/accounts.db` becomes
   `AGENTD_DB_URL=sqlite:////data/accounts.db`, later `postgres://…`. A bare path keeps working as a
   fallback for existing deployments. The swap is then a config value, not a code change.
2. **Keep identity's SQL behind its ports** (§2). `sqlite_refresh_store.py` now,
   `postgres_refresh_store.py` later, chosen by `identity_factory`. Roughly six queries total behind
   an interface. **Do not** add inline SQL to `accounts/app.py` for the new tables the way the money
   endpoints do — that is precisely the code that will be expensive to port.
3. **Write DDL that is already Postgres-legal where that is free.**
   - No `AUTOINCREMENT`. Plain `INTEGER PRIMARY KEY` works in SQLite and maps onto `IDENTITY`.
   - **No `PRAGMA table_info` migrations for the new tables.** `_init_db` currently does
     add-column-if-missing by asking SQLite what columns it has; that has no Postgres equivalent.
     Identity's DDL uses a `schema_version` table instead.
   - Keep epoch floats (`REAL` → `double precision`) rather than moving to `timestamptz`: a
     mechanical port with no semantic change, and the money code already precomputes its `month`
     bucket so nothing depends on date functions.
4. **Decide the parameter style once.** `sqlite3` uses `?`, psycopg uses `%s`, and that difference is
   per-query and pervasive. For identity's small surface, hand-written adapters per backend behind
   the ports beat pulling SQLAlchemy into the accounts image.

### The constraint that actually forces the move

It is not a missing Postgres feature — it is **concurrency**, and it binds sooner than "later".

`/debit` is a read-modify-write: `SELECT` the live grants, then `UPDATE credits_used` on each. There
is no row lock and no compare-and-set. That is safe today only because `desired_count` defaults to
**1** — one process, one file. With two accounts tasks, two concurrent debits can read the same
balance and both succeed, and the resulting overspend is silent. SQLite on EFS compounds it: NFS
file locking is unreliable enough that even the single-writer assumption is not fully sound.

> **Constraint: accounts must stay at `desired_count = 1` until it is on a real database.**

That is a scaling ceiling on the money service only — the daemon and the model proxy scale
independently of it. Identity does not cause this and does not worsen it, but it is the reason the
Postgres move is a scheduled item rather than an open-ended "someday".

### RDS vs Neon

Both are plain Postgres, so the adapter is identical and the choice can wait until
`postgres_refresh_store.py` is actually written. Neon fits the cost shape better — it scales to
zero, so an idle dev stack costs nothing against roughly a $15/mo floor for the smallest always-on
RDS instance. The one thing to check on arrival: Neon prefers pooled/HTTP connections, so the
per-call `connect()` that `_db()` does today would need a pooler in front of it.

## 12. Migration & rollback

- **No account is deleted.** `sub` == the existing `acct_` id, so credits, usage, entitlements,
  state directories and memory partitions are untouched by construction.
- Password hashes are reused as-is by `local_password_provider`. (Optional: upgrade PBKDF2 → argon2id
  by rehashing on next successful login, since we hold the plaintext at that moment.)
- Rollback at any phase = stop issuing JWTs; the legacy path is live until P5.
- Existing signed-in users: their `sess_` token keeps working until it expires (30d) or they
  re-login. No forced logout.

## 13. Risks / open items

- **Clock skew** — 60s `leeway` on verify; NTP on the daemon hosts.
- **Token in the WS query string** — unavoidable for browsers. Mitigated by the 10-minute TTL.
  Optionally use the `Sec-WebSocket-Protocol: bearer, <token>` trick to keep it out of access logs;
  decide at P3.
- **JWKS at boot** — cache to disk so a daemon starting during an Accounts blip can still verify.
  Fail closed only on an unknown `kid`.
- **Refresh stampede vs rotation** — single-flight on the client, and a short grace on the server
  where a token rotated within N seconds returns the same successor instead of tripping reuse
  detection (a legitimate double-submit must not look like theft).
- **Open:** do we want per-agent downscoped tokens at P4 or later? The `scope`/`aud` claims exist
  from P1 either way.
- **Open:** password reset + email verification need an email sender; declared, not scheduled.

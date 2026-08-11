# Sign-in in local mode — one daemon endpoint, no product config

Diagram: `diagrams/agent-builder/agent-builder-signin-flow.puml`

## The problem

`mountSignInGate()` is not a login. It is a payment prompt. Its own default blurb is
*"Runs on our servers — no API keys to set up."* Three consequences, all wrong for a login:

| # | Weld | Where |
|---|---|---|
| 1 | The accounts URL is read by the client from **only** the distribution profile, and by the server from **only** config/env. Same feature, disjoint sources, neither falls back to the other. | `gateway.py:3805` vs `accounts.py:116` |
| 2 | `resolveAuth` returns `needsSignIn: false` as soon as `keysLive` is true — once the proxy pays, it stops asking who you are. | `platform.ts:313` |
| 3 | `signIn()` only succeeds once the model proxy switches on. In BYOK it never does, so a valid login throws. | `platform.ts:242` |

Plus: the session token is persisted as `AGENTD_MODEL_PROXY_KEY` — a payment credential doing
double duty as identity — and it is the only token `publish_agent` can find
(`http_publisher.py:60`). "May I publish?" is answered by "are you paying us?".

Net effect: an agent cannot have a login unless the whole product is rebuilt as a hosted flavour.

## The shape

The daemon already knows how to talk to the accounts service. It should resolve the URL once at
boot and expose **one method every agent calls**. The agent never learns where the accounts
service lives, and no distribution profile is involved.

```
agent ui  ──auth.login{email,password}──▶  daemon  ──POST /login──▶  accounts service
          ◀──────{signedIn:true}────────           ◀───token────────
                                                   stores token locally
```

### Why a WebSocket method, not `POST /auth/login`

The gateway's HTTP handling is the `websockets` handshake hook (`_http_request`, gateway.py:1758).
It receives the handshake request only — **there is no request body**, so a POST with JSON cannot
be read there, and putting a password in a GET query string puts it in logs and history.

Every agent UI already holds a WS connection, and `platform.connect` / `platform.status` are
already app-callable methods over it (`APP_SCOPED_METHODS`, gateway.py:101-103). The reason those
two were *also* given an HTTP route — surviving the live model-proxy reconfigure (gateway.py:1922-
1926) — does not apply to an identity-only login, which reconfigures nothing.

### A free win

Today the **browser** POSTs the password to the accounts server and keeps the token in
`localStorage`. Moving the exchange into the daemon means the page only ever sees
`{signedIn: true, email}`. The session token never reaches page JavaScript.

## The changes

### 1. Resolve the accounts URL once, from all three sources

`accounts.py::configure()` — `AGENTD_ACCOUNTS_URL` > `config.accounts.api_base` >
`distribution.accounts_url`. Same order `registry_url` already uses (`config.py:1050-1056`).
`gateway.py::_platform_status()` reports that resolved value instead of reading the profile
directly, so both halves can no longer disagree.

**Split advertised from enforced.** `accounts.enabled()` currently means both *"clients may sign
in"* and *"clients must present a token or get 4401"* (`gateway.py:2075-2085`). These become two
flags. Having an accounts URL must never gate the machine-token path, or configuring auth locks
the operator out of their own daemon.

### 2. Three new app-callable methods

Added to `APP_SCOPED_METHODS`:

| Method | Params | Returns |
|---|---|---|
| `auth.status` | — | `{available, signedIn, email}` |
| `auth.login` | `{email, password, signup?}` | `{signedIn, email, accountId}` |
| `auth.logout` | — | `{signedIn: false}` |

`auth.login` calls `{accounts_url}/signup` when `signup` is set, then `{accounts_url}/login`,
and persists the returned token as `AGENTD_SESSION_TOKEN` in `~/.agentd/.env`. It returns **no
token**. Failures surface as errors — a bad password is a 401 from the accounts service and is
reported as such, not swallowed into `signedIn: false`.

`available` is false when no accounts URL is configured; the gate then does not render, which is
the one legitimate case of today's behaviour.

### 3. Identity separate from payment

- `AGENTD_SESSION_TOKEN` = who you are. `AGENTD_MODEL_PROXY_KEY` = who pays. Written and cleared
  independently.
- `http_publisher.py::platform_session_token()` reads `AGENTD_SESSION_TOKEN` first, then falls
  back to the existing names so an already-signed-in install keeps working.
- `platform.connect` is unchanged. Cloud mode = sign in **and** connect; local mode = sign in only.

### 4. The gate uses the new method

`gate.ts::mountSignInGate()` → `auth.status`; render when `available && !signedIn`; submit via
`auth.login`. No accounts URL, no `localStorage`, no `keysLive`, no `hosted`.

`platform.ts` keeps `resolveAuth`/`signIn` for the desktop shell's Cloud switch — that is a
billing flow and it still works as designed.

### 5. Rebuild and re-vendor

`cd clients/sdk-js && npm run build` → `tsup` then `vendor.mjs`, which pushes the bundle into
every `agents/*/ui/vendor/` that already has one. Each agent carries a frozen copy, so nothing
sees the change until this runs.

## Files

| File | Layer | Change |
|---|---|---|
| `domain/account_session.py` | domain | **new** — `AccountSession` value object |
| `application/interfaces/platform_accounts.py` | application | **new** — the accounts-service port |
| `application/interfaces/session_token_store.py` | application | **new** — the identity-store port |
| `application/services/sign_in_service.py` | application | **new** — the use case |
| `infrastructure/platform_accounts_http.py` | infrastructure | **new** — httpx adapter |
| `infrastructure/env_file_session_token_store.py` | infrastructure | **new** — `AGENTD_SESSION_TOKEN` |
| `infrastructure/env_file.py` | infrastructure | **new** — `EnvFile`, lifted out of `gateway.py` |
| `infrastructure/accounts.py` | infrastructure | resolved URL; `available()` split from `enabled()` |
| `presentation/gateway.py` | presentation | `auth.*` handlers + allowlist; `_platform_status` uses the resolved URL; four `.env` call sites now go through `EnvFile` |
| `config.py` | — | `accounts_api_base()`; `hosted` derived after the profile loads |
| `runtime_paths.py` | — | `active_config_file()`, `user_env_file()` |
| `main/container.py` | main | `build_sign_in_service()` |
| `clients/sdk-js/src/auth.ts` | clients | **new** — `authStatus/authLogin/authLogout` over the socket |
| `clients/sdk-js/src/gate.ts` | clients | drives off `auth.*`; no `accountsUrl`, no `localStorage`, no `keysLive` |
| `agents/*/ui/vendor/agentd-client.js` | — | regenerated by `npm run build`, never hand-edited |
| `agents/agent-builder/skills/.../SKILL.md` | — | `auth.*` documented; the drift test requires it |

## Tests

- The URL resolves from each of the three sources, in precedence order.
- Advertising a sign-in server does **not** close a machine-token connection (pins the 4401 lockout).
- `auth.login` with a wrong password returns an error, not `signedIn: false`.
- `auth.login` never includes a token in its reply.
- `auth.status.available` is false with no URL configured.
- `platform_session_token` finds the identity token with no proxy key present.
- Sign-in works with the model proxy off (the BYOK case).

## Order

1 → 2 → 3 → 4 → 5. Steps 1+2+4 make the login appear and work in local mode; step 3 is what makes
publishing work while signed out of billing.

## Out of scope

- Per-agent accounts services. One daemon, one identity service.
- The `publish_agent` / `_SHIPS_BROKEN` gap — other developer's branch.
- Marketplace roster admission (needs the offline root key).

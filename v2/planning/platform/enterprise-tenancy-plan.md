# Enterprise tenants (organizations) — research + implementation plan

> **Status:** BUILT (E1+E2+E3+E5 + admin panel, 2026-08-19, uncommitted; research 2026-08-16).
> Daemon restart + client rebuild needed to go live. Verified by 2069 passing unit tests
> (56 org tests across test_orgs_api.py / test_org_tenancy.py / test_org_token_claim.py /
> test_metering.py), typecheck clean on all four client workspaces, web bundle built,
> import-linter contracts green. What shipped, per phase:
>
> - **E1** — identity `_STEPS` step 2 (orgs/org_members/org_domains/org_invites), the
>   `orgs` JWT claim end-to-end (Principal → issuer → AccessClaims → JWKS verifier, one
>   `orgs_from_wire` codec; personal tokens carry NO claim, byte-identical), `accounts/orgs_api.py`
>   (all 8 routes, fail-closed 404s, single-use hashed invites, seats gate 409), login
>   `joinable_orgs` offer, `/resolve` orgs.
> - **E2** — `org_id` on credit_grants + usage (partial tenant-leading indexes); two pockets
>   never mix (`_live_grants` org branch also requires org.active=1); per-member monthly cap =
>   min(pool, cap-left) at the same funding gate with `member_capped` on the 402; proxy threads
>   `X-Agentd-Org-Id` (trace → funding → debit → usage row); RunContext.org_id set from the
>   resolved agent's owner in agent_service + the tools.invoke path; model_proxy.apply stamps it.
> - **E3** — `orgs/<org_id>/agents/` root + `identity_root` mapper in user_state (used by
>   `_file_roots_for`); tenant_scope org definition read-grants (per-agent `definition_entries`,
>   clamp never widens); `ownership.callers(..., org_ids)` (org_ prefix-filtered, ride the
>   account only); FileAgentRegistry ordered layers curated < org < personal with the owner
>   filter widened to the identity set, org agents owned by ADMINS not members;
>   `agents.shareToOrg` / `agents.unshareFromOrg` RPCs (definition-only copy, ownership record
>   owner=org origin=installed, originals untouched).
> - **E5** — ProfileMenu Organizations section (memberships + offer dot), OrgView
>   (overview: create/join/redeem-invite; detail: members/roles/caps/invites/domains/usage),
>   My Agents per-org sections from `scope:'org'`+`orgId` rows, Share-to-org / Remove-from-org
>   on cards; `_platform_status.orgs`; `lib/orgs.ts`.
> - **Admin** — `/admin/orgs` list (seats/pool/month), grant-to-pool, suspend/reinstate
>   (suspension kills routes + pool + next-token claim together).
> - **E4 leftovers still open:** rulebook gaps 7/8/10/15 + the machine-wide credential vault
>   (pre-launch blockers, unchanged), Neon/RLS later. The share RPC runs the registry's
>   structural guards (definition-only copy, valid id, agent.toml required) — the agent-builder
>   rulebook gates stay on the PUBLISH path (its plugin is not importable from core).
>
> The public marketplace remains on hold; My Agents replaced it in the client (built earlier).

## Context

Today every tenant is one person: an `acct_` row owns its credits, its agents, its workspaces and
its chats, and the hosted daemon fences accounts from each other (per-account state roots, the
tenant fs fence, `ownership.may_observe` at both egress choke points). What does not exist is any
grouping above a person. The target scenario: **Kajima Corporation has agents its employees use;
credits are bought by the company, granted per seat, and spent by employees across the org's
agents — and no two enterprises can ever see each other's workspaces or memory.**

The design principle that keeps this small: **an organization is not a new kind of tenant — it is
a tenant that accounts are MEMBERS of.** Personal use stays the degenerate case (an implicit org
of one), exactly the way desktop-local is already the degenerate one-account case of hosted. No
mode branches; the org layer is data.

---

## What the research says (2026 survey — full citations at the bottom)

**Org model.** The industry converged on the Notion/ChatGPT shape, not Slack's: ONE user identity,
member of N workspaces, with a workspace switcher. Roles are Owner/Admin/Member with exactly one
Primary Owner (Claude for Work). Domain-based auto-join is table stakes: Notion's "allowed email
domains" with a cheap guardrail — you may only allow a domain that an existing member's email
already uses. DNS-verified domains gate the stronger enterprise features (Figma's domain capture,
Anthropic's "restrict org creation") and belong to a later tier, with SSO/SCIM.

**Billing.** The converged model for AI platforms: **per-seat flat fee buys access + a baseline;
expensive/agentic usage draws from an org-level pooled bucket; overage is opt-in at metered rates;
per-member caps are admin POLICY, not pricing.** ChatGPT Enterprise pools credits org-wide with
RBAC spend controls; Copilot ships per-seat premium-request buckets with an org overage toggle
(default on); Anthropic and Cursor both landed independently on "premium seat = 5x usage" for
power users; Lovable/Replit run pure pooled credits but re-grow per-member caps as an admin
control. Slack's fair-billing (only pay for seats active in a 28-day window) is the seat-hygiene
idea worth copying eventually.

**Data architecture.** AWS's silo/pool/bridge vocabulary, applied per-resource: pooled tables +
tenant id for the self-serve tier, silo for premium/regulated tenants. For our SQLite → Postgres
path: shared tables + `org_id` columns now (written portably, the way `identity/sqlite_schema.py`
already does), Postgres **row-level security as the enforcement backstop** when we land on Neon —
with the three documented footguns designed against from day one: `SET` instead of `SET LOCAL`
leaks tenant context across pooled connections (this exact class caused the ChatGPT/Redis leak of
March 2023); a missing tenant variable must fail CLOSED; tenant id must lead every index. Neon's
own recommendation for the silo tier is **project-per-tenant** via their API (millisecond
provisioning, scale-to-zero, per-tenant PITR) — the natural home for an enterprise that pays for
hard isolation.

**Compute isolation.** The ladder: (a) shared process + path fencing → (b) process-per-tenant →
(c) container/gVisor → (d) Firecracker microVM. Our `tenant_scope` read_roots/write_clamp fencing
is tier (a); the industry treats that as necessary depth but not a sellable boundary. The credible
2026 boundary for untrusted agent code is (c)/(d) — E2B/Fly-style microVM-per-tenant now starts in
~200ms and scales to zero, the same economics as Neon's projects. LangGraph's lesson for early
enterprise deals: a **hybrid data plane in the customer's VPC** often substitutes for hard
multi-tenant walls entirely and is cheaper for a small team than building them.

**Failure modes to design against** (each has a named real-world incident): tenant context on a
pooled resource (ChatGPT/Redis); the forgotten-`WHERE tenant_id` class (killed by scoped query
helpers + RLS backstop + fail-closed context); isolation granularity coarser than the trust
boundary (Code Interpreter files bleeding between GPT sessions in one user's container — for us:
org-shared AGENT, per-member private CHATS); customer-facing features as the bridge across the
tenant wall (all four Wiz cross-tenant CVEs). Wiz's PEACH framework is the review rubric.

---

## The design on one page

```
accounts (unchanged)          orgs (new)
acct_a ──member(owner)──────► org_kajima ── seats_total: 50
acct_b ──member(admin)──────►    │            allowed domain: kajima.co.jp
acct_c ──member(member)─────►    │
                                 ├── credit pool:   credit_grants rows with org_id = org_kajima
                                 ├── org agents:    <state_dir>/orgs/org_kajima/agents/<id>/
                                 └── per-seat caps: org_members.monthly_credit_cap (policy)

WHO PAYS: seat = membership row (access). Usage draws org pool FIRST when the turn runs an
          org agent, personal grants otherwise. Per-member caps checked at the same gate.
WHO SEES: agent DEFINITIONS shared org-wide (read-only overlay, like the curated catalogue);
          every member's CHATS/workspace/memory stay under their OWN account subtree — org
          sharing never widens what one member can read of another.
```

### The three seams (verified in-code — the whole org layer attaches here)

1. **`ownership.callers()`** (`domain/ownership.py:108`) — the ONLY producer of the identity set.
   Everything downstream already consumes a frozenset: `may_observe`, the `_send_all` egress
   fence, `_file_roots_for` (HTTP files), `client_identities` (per-socket), `registry.owns()`.
   Org ids added here propagate to every fence with almost no downstream change.
2. **`user_state.tenant_scope()`** (`infrastructure/user_state.py:158`) — the ONLY producer of
   filesystem grants (gateway.py:1538 applies it). The module declares itself the layout
   authority; an `orgs/<org_id>/` root is additive here and nowhere else.
3. **`_live_grants()` / `_funding_view()`** (`accounts/app.py:833/:875`) — the ONLY producer of
   spendable money, already unioning multiple `scope` values with a deterministic
   soonest-expiring-first drain.

Other load-bearing facts: the model proxy already threads `agent_id` per call via the trace and
asks `/funding` before every uncached call — org draw is a change inside `_funding_view`, not a
new hop. The admin console provides the operator surface (an Orgs tab behind the same
`_require_admin` door). The `identities` table (`identity/sqlite_schema.py`) is the pattern org
membership follows, in the same versioned-steps migration ledger.

### The four places needing new STRUCTURE, not just new values (verified)

- **`FileAgentRegistry` supports exactly one overlay.** `_current()` (:454) merges
  `{**shared, **overlay}` and filters shared-layer owners against a hardcoded 2-tuple
  `(PLATFORM_OWNER, acct)` (:475). Org-shared agents need an ordered layer list
  (curated < org < personal) and an owner filter that takes the identity SET. The overlay cache
  (`self._overlays`, path-keyed) already tolerates N; `_current()` does not.
- **`RunContext` carries no tenant identity** (`application/run_context.py:16`) — only derived
  read_roots/write_clamp. Org attribution for funding needs a field; the application layer may
  not import `accounts` (importlinter), so the value must be injected where RunContext is built.
- **`_file_roots_for`** (gateway.py:2156) calls `account_root()` for EVERY identity in the set —
  handed an org id it would silently mint `accounts/<org_id>`, conflating namespaces. It needs
  an identity→root mapper (`acct_*` → accounts/, `org_*` → orgs/).
- **`creators` is PK'd on `account_id`** (1 creator : 1 account), and `bundle_owners` holds a
  single irreversible `creator_id` — "publish as my org" is unrepresentable. E3 sidesteps the
  registry entirely (org sharing installs into the org ROOT, no publish), so this only binds the
  later "org publishes to the public marketplace" feature — noted, not needed now.

---

## Phase E1 — the org primitive (accounts service)

New tables in **`identity/sqlite_schema.py::_STEPS` as step 2** — not in `accounts/app.py`. Org
membership is identity ("who is this person, in which contexts"), and the identity file IS the
portable migration ledger (no `PRAGMA table_info`, no `AUTOINCREMENT`, epoch floats): the whole
reason it exists is so these tables port to Postgres mechanically. The routes live in a new
`v2/accounts/orgs_api.py` router, composed in `app.py` exactly the way `admin_api.py` was.

```sql
CREATE TABLE orgs (
    id            TEXT PRIMARY KEY,          -- 'org_' + hex, minted like acct_
    name          TEXT NOT NULL,
    primary_owner TEXT NOT NULL REFERENCES accounts(id),   -- exactly one; the recovery anchor
    seats_total   INTEGER NOT NULL DEFAULT 5,
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    REAL NOT NULL
);
CREATE TABLE org_members (
    org_id     TEXT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    role       TEXT NOT NULL DEFAULT 'member',   -- owner | admin | member
    -- POLICY, not pricing (Lovable/ChatGPT pattern): 0 = uncapped
    monthly_credit_cap INTEGER NOT NULL DEFAULT 0,
    added_by   TEXT NOT NULL DEFAULT '',
    added_at   REAL NOT NULL,
    active     INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (org_id, account_id)
);
CREATE TABLE org_domains (   -- allowed email domains (auto-join); verified = DNS tier, later
    org_id   TEXT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    domain   TEXT NOT NULL,                  -- lowercased, no '@'
    verified INTEGER NOT NULL DEFAULT 0,
    added_by TEXT NOT NULL DEFAULT '',
    added_at REAL NOT NULL,
    PRIMARY KEY (org_id, domain)
);
CREATE TABLE org_invites (
    token_hash TEXT PRIMARY KEY,             -- sha256; plaintext returned once, never stored
    org_id     TEXT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    email      TEXT NOT NULL DEFAULT '',     -- '' = open link
    role       TEXT NOT NULL DEFAULT 'member',
    expires_at REAL NOT NULL,
    created_by TEXT NOT NULL,
    used_by    TEXT NOT NULL DEFAULT ''
);
```

**How the daemon learns membership: an `orgs` claim in the access token.** The token issuer adds
`orgs: [{id, role}]` at mint; `accounts.resolve()` already verifies tokens locally against JWKS
(accounts.py:253-277), so the daemon learns org membership **with zero new hops at connect** and
`ownership.callers()` can widen the identity set from the claim alone. Fail-closed: no claim = no
org context. Membership changes propagate within the access-token TTL (10 minutes) — the same
revocation latency the platform already accepts for account state, and removal-from-org can ride
the existing sessions-revoke path when it must be immediate. The client never asserts an org id;
it only selects among the ones its token already carries.

Routes (all bearer-token, membership-checked per call — **the org id is NEVER trusted from the
client without a membership row**, fail-closed):

| Route | Who | Does |
|---|---|---|
| `POST /orgs` | any account | create; caller becomes primary_owner + owner member |
| `GET /me/orgs` | member | my orgs + my role (the switcher's data) |
| `GET /orgs/{id}` | member | detail; members list only for admin+ |
| `POST /orgs/{id}/invites` | admin+ | mint invite (returns link once) |
| `POST /orgs/join` | any | by invite token, or by email-domain match against org_domains |
| `POST /orgs/{id}/members/{acct}` | admin+ | role change / cap / remove (owner immune to admin) |
| `POST /orgs/{id}/domains` | admin+ | free text for now — **no verification, no guardrail** (user call, 2026-08-18); the Notion guardrail + DNS verification are later hardening |
| `GET /orgs/{id}/usage` | admin+ | month rollup from `usage` by member (rides new org_id column) |

Domain auto-join hook: after `/auth/login` and `/signup` succeed, a cheap
`SELECT org_id FROM org_domains WHERE domain = ?` on the email's domain; a hit surfaces
`joinable_orgs` in the login response for the client to offer — never silent auto-add (Notion
asks; silent membership is how a contractor lands inside the wrong wall).

Platform-admin surface: an **Orgs tab** in the admin console (list, seats, grant to pool,
suspend), one more panel behind `_require_admin`.

## Phase E2 — org money: seats as access, credits as a pool

**Additive migration** (the accounts style — `PRAGMA table_info` guarded, indexed after):
`credit_grants` and `usage` each gain `org_id TEXT NOT NULL DEFAULT ''`.

- **Granting to the pool**: `_apply_grant` accepts `org_id`; the ledger posting carries it. The
  admin console's grant action gains an org target. (Mock-purchase for orgs = same
  NullPaymentGateway path, a `credit_pack` product with an org buyer — the rail already
  post-processes purchases generically.)
- **Drawing from the pool** (`_funding_view` / `_live_grants` / `/debit`): the funding request
  already carries `account_id` + `agent_id`; it gains `org_id` (threaded from the daemon per
  turn, below). Draw order: **org pool first when the turn is org-attributed, personal grants
  otherwise; never silently cross** — an employee's personal credits must not fund company work
  by accident, and vice versa. Per-member cap: when drawing org pool, sum this member's
  month-to-date org-funded `usage.credits` against `org_members.monthly_credit_cap`; at/over =
  the same 402 the personal path already produces (message names the cap).
- **Seats gate membership, not model calls**: adding a member past `seats_total` refuses at the
  membership route (Figma true-up can come later). The model-call gate stays purely credits —
  one gate per question.
- **Attribution**: the daemon already sends a trace with `agent_id`; it gains `org_id` for turns
  on org agents. `usage.org_id` makes the org usage rollup and the per-member cap the same
  indexed query shape the admin console already uses (`month`-led indexes).

## Phase E3 — org agents (the Kajima agent its employees use)

**Definitions shared, data private.** This is the curated-catalogue split applied to a new root —
the isolation granularity lesson from the Code Interpreter incident, encoded structurally:

- Org agents live at `<state_dir>/orgs/<org_id>/agents/<agent_id>/` (definition: agent.toml, ui/,
  plugins/, skills/ — exactly the catalogue shape).
- **The registry work is structural** (see "four places" above): `FileAgentRegistry._current()`
  goes from one overlay to an ordered layer list — **curated (shared, RO) < org (RO for members,
  admin-managed) < personal (RW)** — personal winning collisions, the connection's identity SET
  (from the token's orgs claim) deciding which org layers exist for it, and the shared-layer
  owner filter widening from the hardcoded `(PLATFORM_OWNER, acct)` 2-tuple to that same set.
  `_file_roots_for` gains the identity→root mapper at the same time (`org_*` → `orgs/`, never
  `accounts/`).
- Every member's chats/workspace/memory for an org agent stay at
  `<state_dir>/accounts/<acct>/agents/<agent_id>/…` — the account-keyed data path is UNCHANGED,
  so two employees using the company agent still cannot see each other's chats, and
  `ownership.may_observe` needs no new rules (sessions remain account-owned).
- `tenant_scope` read_roots for a member += their orgs' definition dirs (read-only, the same
  `definition_entries` shape the sandbox grant already uses). Non-members: unchanged — the org
  root is simply never in scope, which is what "no two enterprises step on each other" means at
  the filesystem.
- **Sharing an agent to the org**: `agents.share_to_org` RPC — packs the caller's agent (the
  existing pack path with its rulebook checks) and installs into the org root; org admin+ only,
  or any member with admin approval later. Unsharing removes the org copy; personal originals
  are never touched. The private-tool map is already keyed by LOCATION + content hash, so an org
  copy's plugins are distinct modules from the author's originals — no aliasing.
- Org cron: org agents may carry schedules; cron rows are already partitioned by agent — they
  gain the same org attribution for funding (E2's trace).

## Phase E4 — enforcement backstops (the bug-class killers, from day one)

1. **Org context is per-connection state, never ambient.** It rides the same per-connection
   identity the account already does (verified at connect against accounts, cached on the
   socket), and the same per-turn contextvar — never a module global, never an env var. This is
   the `SET LOCAL` lesson applied to our transport.
2. **Fail closed everywhere**: an org id without a live membership row = 403 on HTTP, connection
   refusal on WS, empty layer in the registry union. Absent org context = personal scope, never
   "all orgs".
3. **Membership tests in the admin-door style**: for every org route, the non-member, the
   removed member, and the member-of-a-DIFFERENT-org each get refused; the fs fence gets the
   equivalent (a connection with org A in scope resolves nothing under org B's root). These are
   the `test_admin_api.py` shapes with org ids.
4. **Close the four known per-account gaps BEFORE org launch** — they are single-tenant warts
   today and cross-ENTERPRISE leaks tomorrow (rulebook gaps, `agent-rulebook.md:126-165`):
   `resources.sqlite` unpartitioned (gap 10), heartbeat runs carrying no account (gap 7), cron
   bypassing the budget gate (gap 8 — fatal once cron can drain an org pool), and
   visitor-triggered web-app installs mutating the SHARED catalogue (gap 15). Also note the
   credential vault (`credentials/file_store.py`) is machine-wide with no account scoping — org
   agents must not gain secrets through it until it is partitioned.
5. **Neon later**: org_id/account_id RLS policies (`FORCE`, non-owner role, `SET LOCAL`,
   fail-closed missing context, tenant-id-leading indexes) as the backstop under the same
   queries; **project-per-tenant silo** offered as the paid enterprise tier when a contract asks
   — per-resource silo/pool, not a global rewrite. Compute isolation upgrades ((c)/(d): gVisor /
   Firecracker per tenant) attach at the existing `PluginSandbox` port; until then the fence is
   tier (a) and we SAY so to enterprise prospects — or sell the LangGraph-style answer (their
   VPC) first.

## Phase E5 — client

- **Org switcher** in ProfileMenu (ChatGPT pattern): the orgs come from the token's own claim
  (surfaced through `_platform_status`, which is where the client already reads
  `{signedIn, accountId}`); active org in localStorage; the socket reconnects with the selection.
  Every list is already server-scoped, so the client changes are presentation only.
- **Org agents render from data, not client logic** — the `_agents_list` rows already carry
  `mine`/`origin`; they gain `scope: 'org'` + the org name, the same
  capabilities-as-data rule the rest of the codebase follows.
- **Org page** (members, seats, invites, domains, usage) — admin-console table vocabulary,
  visible to org admins, reachable from the switcher.
- **My Agents** gains an "Organization" section when an org is active: the org layer's agents,
  with the same share doors; "Share to organization" action on the user's own published cards.
- Sign-up/login: render `joinable_orgs` when the response carries it.

## Deliberately NOT now

Domain checks of ANY kind — an org admin types any domain string and it joins matching emails,
nothing verifies it (explicit user decision 2026-08-18; the Notion member-email guardrail and DNS
verification are the hardening step before real enterprises). SSO/SCIM; Stripe org billing (mock
rail, real accounting — unchanged posture); premium seat tiers (the 5x idiom is noted for later);
Slack-style fair billing; compute silos and Neon projects (named upgrade paths, not P0); anything
cross-org.

## Sequencing and verification

Build order **E1 → E2 → E3** (each independently shippable; E5 lands with its phase's surface;
E4 is woven through, not a phase). Verification per phase:

- **E1**: unit tests for every membership refusal + the domain guardrail; e2e: create org on web,
  invite second account, domain-join a third.
- **E2**: grant to pool → member spends on an org agent → `usage.org_id` row lands → org rollup
  moves → cap at N refuses the N+1th credit with 402; personal spend untouched throughout.
- **E3**: share agent to org → second member sees it in My Agents and chats → their session is
  invisible to the first member; a NON-member's connection resolves nothing under the org root
  (the fence test); uninstall-from-org leaves the author's original intact.
- Throughout: the existing tenant-isolation suites keep passing byte-identical on desktop (no
  org context = today's behaviour, the degenerate case).

## Sources

Org model: [Slack Enterprise Grid](https://slack.com/help/articles/115005474583) ·
[Notion domains](https://www.notion.com/help/domain-management) ·
[Figma domain capture](https://help.figma.com/hc/en-us/articles/360045953273) ·
[GitHub EMU vs BYOU](https://docs.github.com/admin/identity-and-access-management/understanding-iam-for-enterprises/choosing-an-enterprise-type-for-github-enterprise-cloud) ·
[ChatGPT workspaces](https://help.openai.com/en/articles/8801848) ·
[Claude for Work roles](https://support.claude.com/en/articles/13133750).
Billing: [ChatGPT flexible pricing](https://help.openai.com/en/articles/11487671) ·
[Claude Team](https://support.claude.com/en/articles/9266767) ·
[Cursor Teams 2026](https://cursor.com/blog/teams-pricing-june-2026) ·
[Copilot overage](https://github.blog/changelog/2025-08-22-premium-request-overage-policy-is-generally-available-for-copilot-business-and-enterprise/) ·
[Lovable](https://lovable.dev/pricing) · [Replit](https://docs.replit.com/billing/teams-billing/overview) ·
[Slack fair billing](https://slack.com/help/articles/218915077).
Data: [AWS silo/pool/bridge](https://docs.aws.amazon.com/whitepapers/latest/saas-tenant-isolation-strategies/the-bridge-model.html) ·
[Neon multitenancy](https://neon.com/docs/guides/multitenancy) ·
[Neon db-per-tenant](https://neon.com/use-cases/database-per-tenant) ·
[RLS footguns](https://patotski.com/blog/postgres-row-level-security-multi-tenant/) ·
[Turso](https://turso.tech/multi-tenancy).
Isolation: [E2B/Modal/Fly comparison](https://northflank.com/blog/e2b-vs-modal-vs-fly-io-sprites) ·
[Cloudflare data isolation](https://developers.cloudflare.com/use-cases/saas/data-isolation) ·
[LangGraph deployment options](https://github.com/langchain-ai/langgraph/blob/main/docs/docs/concepts/deployment_options.md).
Failures: [ChatGPT Redis bug](https://thehackernews.com/2023/03/openai-reveals-redis-bug-behind-chatgpt.html) ·
[Wiz PEACH](https://www.wiz.io/blog/introducing-peach-a-tenant-isolation-framework-for-cloud-applications) ·
[Code Interpreter bleed](https://embracethered.com/blog/posts/2024/lack-of-isolation-gpts-code-interpreter/) ·
[Rails tenant layer](https://dev.to/temitopeajao/row-level-multitenancy-in-rails-building-a-bulletproof-tenant-isolation-layer-from-scratch-25de).

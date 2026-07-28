# agentd Platform Plan

**Status:** design, under review — no infra provisioned, no cloud code written.
**Last updated:** 2026-07-21.
**Diagrams (source of truth, render on demand):** `diagrams/platform-architecture.puml`
(components, planes, run×pay, request flow, share flow), `diagrams/aws-deployment.puml`
(AWS topology + phases), `diagrams/isolation-model.puml` (tenancy walls + cross-turn
continuity), `diagrams/dispatch-seam.puml` (one execute(), two executors).

---

## 1. Context — why this exists

pc-agent v2 (agentd) is a working local-first agent engine: a folder is an agent
(`agents/<id>/`), everything is data, nothing is hardcoded, and it runs on the user's
machine today. The goal is to let **anyone create an agent as a folder, share a link, and
have a recipient run it within seconds** — in a browser or as a desktop exe — on **our**
infrastructure, using **our** models (subscription) or their own key (BYOK), with **strict
per-user isolation** and **no change to how agents are authored**.

This is the Cursor/Replit shape (local + hosted app, models rented not owned, subscription
or BYOK) plus one differentiator none of them have: **agents as shareable folders on shared
infrastructure.** We are explicitly NOT building the ChatGPT/Gemini model-ownership layer —
we rent intelligence through a gateway.

The plan below is the reassessment that replaces the earlier single-EC2 demo Terraform
(now deleted): it separates concerns that were previously blurred and pins the isolation and
execution model before any infra is written.

---

## 2. The four components (hard boundaries)

> Diagram: `platform-architecture.puml → components`.

1. **Core engine (agentd)** — the interpreter of agent folders. Same code on the user's
   machine or our fleet. It *consumes* a model endpoint + a token; it **never owns** accounts,
   billing, provider keys, or the storefront. Its **built-in** tools (fs, web, shell, figures…)
   ship inside its image because they are *software*. Nothing user-authored lives in it.
2. **Platform Server** — three small services that serve **every** user regardless of where
   their engine runs:
   - **Accounts** — sign-in; issues the account token.
   - **Model Proxy** ("the Cursor server") — holds OUR provider keys, meters per account,
     enforces plan/limits. BYOK bypasses it.
   - **Marketplace Registry** — stores published `.agentpkg` + listings; the listing IS the
     share link. It runs **no** agents.
3. **Hosted Runtime** — our fleet running engines for **browser** users; multi-user, per-user
   state. A **separate concern** from the Platform Server (blurring these two caused the
   earlier confusion).
4. **Agents** — pure content (`.agentpkg`: identity, UI, skills, optional custom plugins).
   Created as folders, published, executed by whichever engine the user has. Never baked into
   the image.

**Boundary rules (invariants):**
- The engine consumes tokens + endpoints; it owns no accounts/keys/billing/storefront.
- The platform server runs no agents.
- Agents are data; they never know where they run or how models are paid.
- **Rule #5: engine boxes hold no user data, ever.**

---

## 3. The three planes

> Diagram: `platform-architecture.puml → planes`, and `aws-deployment.puml`.

- **Compute plane** (cattle — disposable): the engine fleet, the model proxy, sandbox
  runners. Kill/replace/scale freely. Holds nothing durable.
- **State plane** (crown jewels — durable, isolated per account, backed up): accounts DB,
  per-user state roots, uploaded files, our secrets.
- **Distribution plane**: the marketplace registry (just storage + a DB; serves both desktop
  and web installs).

Rule #5 restated operationally: a compute box can die and be replaced with zero loss because
all durable state lives in the state plane. **Desktop is the extreme case — the user's own
machine IS their state plane; their data never reaches our cloud, only identity + model calls
do.**

---

## 4. Two axes of freedom (Cursor model)

> Diagram: `platform-architecture.puml → run-x-pay`.

Independent per user: **where the engine runs** (desktop / browser) × **how models are paid**
(BYOK / our subscription). All four combinations are the same engine; the only thing that
changes is one seam — `model endpoint + credential` = `(provider, their key)` or
`(our gateway, account token)`.

| | BYOK | Subscription (our keys) |
|---|---|---|
| **Desktop / exe** | today's mode; platform not in the path | the Cursor model — sign in, local engine calls our gateway |
| **Browser** | their key in their account vault (later) | the ChatGPT model — engine + state + models all our side |

---

## 5. AWS topology — what runs where

> Diagram: `aws-deployment.puml`. Cloud-role names below; AWS specifics are our current choice,
> not a hard commitment (every role maps to any cloud).

**Compute plane**
- **ECR** — stores engine / gateway / sandbox images.
- **Model Proxy** — ECS Fargate service; LiteLLM proxy; our keys via Secrets Manager;
  per-account virtual keys, budgets, metering → RDS. Serves desktop AND web.
- **Platform / Registry API** — ECS Fargate service; sign-in glue, publish, entitlements,
  agent provisioning.
- **Hosted Runtime engine fleet** — ECS Fargate service; the agentd image, multi-user;
  per-connection user_id → that user's state.
- **Sandbox runner** — ECS Fargate task, on-demand; runs an agent's CUSTOM plugin code in
  isolation; mounts only that user's agent dir; dies after the run.

**State plane**
- **RDS (PostgreSQL)** — accounts, subscriptions, listings, entitlements, usage/metering rows.
- **EFS** — per-user state roots `/users/<uid>/agents · sessions · memory` (mounted by the
  fleet; access points scope the sandbox mounts).
- **S3** — `.agentpkg` blobs, uploaded files, built `.exe` installers.
- **Secrets Manager** — our provider API keys; readable ONLY by the Model Proxy.

**Edge / identity**
- **Cognito** — accounts; issues the account token.
- **CloudFront + ALB + Route 53** — TLS, WebSocket proxy, per-agent vanity URLs.
- **CloudWatch** — logs/metrics/alarms.

---

## 6. Isolation & execution model (the security core)

> Diagrams: `isolation-model.puml`, `dispatch-seam.puml`.

**Honest starting point:** today the agent's tools are deliberately UNRESTRICTED — `find`
walks the whole machine, `exec` runs any shell command, and the `is_under_roots` containment
helper is used only by the gateway's HTTP file serving, not by the agent's tools. That is
correct for a desktop PC-agent and a from-scratch requirement for multi-tenant.

**A path-string check can never be the guarantee.** `exec` alone defeats it (a shell reads
anything the process can), and path checks have a long history of bypasses (symlinks,
normalization, TOCTOU). Therefore:

**The guarantee is the OS/kernel/mount boundary, not application path logic.** Four
independent walls:
1. **Identity** — user_id comes from the verified account token, never the client.
2. **Paths (defense-in-depth)** — the shared engine roots every read at `/users/<uid>/` AND
   never exposes exec / custom-plugin tools.
3. **Kernel/mount (the guarantee)** — arbitrary code runs ONLY in a sandbox mounting just that
   user's dir (EFS Access Point); a neighbour's path is physically absent.
4. **IAM/network** — the sandbox role reaches only that user's storage; no provider keys, no
   DB creds, no internal VPC.

**Trusted vs untrusted split:** built-in tools (in the image) run in-process; the agent's own
custom plugins and `exec` are **banished from the shared engine** and dispatched to a per-run
sandbox.

**Continuity via durable state, not a live process:** a sandbox is ephemeral. Tools write
durable outputs into the mounted workspace (EFS/S3); the reference is recorded in the session
transcript; the next turn's fresh sandbox mounts the same dir and reads it back. Scratch inside
the VM vanishes; the workspace persists.

**The dispatch seam — one `execute()`, two executors:** the tool author writes one
`async execute(params) -> ToolResult`. The engine picks the executor by **provenance**
(built-in vs user-authored) and **runtime** (desktop vs web):
- **Local executor** (desktop, and trusted built-ins on web) — in-process/subprocess; model
  seam → user's key or gateway; fs → whole machine (desktop) or user root (web).
- **Sandbox executor** (web: custom plugins, exec, heavy) — dispatch to the sandbox; model
  seam → a short-lived, spend-capped **virtual key** minted by the Model Proxy; fs → mounted
  user dir only.

Tool code is identical because two conventions already hold in agentd: tools get their model
through the seam (never a raw key), and tools write to `current_workspace()` (never a fixed
path). **Desktop today already IS the local executor.**

### 6.1 File access in practice — how a tool actually reads a user's files

A concrete walk-through of the question "the `ls`/`find`/`read` tools live inside the daemon
container — how do they reach a given user's files?", because the mechanic is where the
guarantee is easy to get wrong.

- **The tools ARE in the container, and that's fine.** `ls`, `find`, `read_file`, `write_file`
  work on *paths* — ordinary "list this folder / open this file" OS calls. When **EFS is mounted
  at `/data`**, that folder *is* the network drive; to the tool it looks like a local directory
  and it cannot tell the bytes come over the network. So the tools need **zero changes** to run
  on EFS — EFS is transparent. (This is the same reason desktop tools already read a local disk.)
- **How the tool is pointed at the right user's folder.** Before a turn runs, the engine pins
  this run's workspace/state root to that user's per-account dir (A1, threaded via `RunContext`;
  `current_workspace()` → `/data/accounts/<uid>/agents/<agent>/workspace/`). So `ls`/`find` with
  no argument, and every relative path, resolve **inside that user's subtree** on EFS. A read is
  then just a normal filesystem call the OS routes to EFS.
- **Why the folder split alone is NOT the wall.** A shared engine has *every* user's folder under
  the same `/data` mount. A tool that takes an **absolute path** (`find /data`, or worse `exec`,
  which reads anything the process can) could step outside the user's subtree. So per-account
  **folders are a correctness boundary, not a security guarantee** — exactly the point of §6:
  a path-string check can't be the wall.
- **Where the real guarantee comes from (§6, walls 3–4).** For trusted built-in tools on a
  curated shared engine we add **defense-in-depth path-scoping** (root reads at `/users/<uid>/`)
  AND exclude `exec`/custom plugins. For anything that runs arbitrary code, the run goes to a
  **sandbox whose EFS mount is scoped by an Access Point to just that user's subtree** — a
  neighbour's path is *physically absent from the filesystem*, so even `find /` sees only their
  own files. The wall is **what we mount into the box**, never trust in the tool.

**Rollout consequence:** curated first-party agents (safe tool allow-lists, no `exec`) can run on
the shared engine with path-scoping now; opening the doors to third-party/custom-code agents is
gated on the sandbox + access-point work (M5). This is why the milestone order is curated-first.

**Desktop is unaffected.** Web confinement is a **mode knob (A3)**, off on desktop: the desktop
PC-agent keeps deliberate **whole-machine** file access (that's the product — "find that file on
my D: drive"). There is no EFS, no shared multi-user mount, and no sandbox on desktop; the
per-account path routing is a no-op when accounts are off. **Rule #5 restated for files:** the
engine box holds no durable user data — on the hosted side it lives on EFS/S3; on desktop the
user's own machine *is* their state plane.

---

## 7. The build — two tracks

**Track A — agentd CORE (code; cloud-agnostic; makes one engine multi-tenant + sandbox-ready).**
Desktop behavior is unchanged throughout (it is the degenerate single-tenant / local-executor
case).

- **A1. Parameterize the state root** — replace the single boot-time `config.state_dir`
  (file_registry, sessions, memory, task store are closures over it) with a per-connection
  resolver keyed on user_id, threaded via `RunContext`.
- **A2. Identity at the gate** — resolve user_id from a verified token; pluggable (local
  single-user token vs Cognito). Generalizes the public-tier connection gate already built.
- **A3. Web-mode confinement** — fs tools scoped to the user root; `exec` + custom plugins
  removed from the shared engine's toolset (available only via the sandbox executor). A mode knob.
- **A4. Executor seam** — formalize `Tool.execute` dispatch into Local vs Sandbox executors.
- **A5. Model seam mode** — point the existing model resolution (resolve_tool_model → litellm)
  at the gateway with a supplied credential (BYOK / gateway / virtual key).

**Track B — PLATFORM (our services + AWS infra; drawn in `aws-deployment.puml`).**

- **B1. Model Proxy** — LiteLLM proxy; keys in Secrets Manager; per-account virtual keys,
  budgets, metering → RDS.
- **B2. Accounts** — Cognito + desktop `agentd login` device flow.
- **B3. Registry/Platform API** + S3 + RDS — publish, listings, entitlements, hosted marketplace.
- **B4. Hosted Runtime** — engine image (done) on Fargate + ALB + CloudFront + EFS per-user roots.
- **B5. Sandbox plane** — runner image, ECS RunTask orchestration, EFS access points, IAM roles,
  virtual-key mint integration.

**Track C — CLIENTS (built FIRST; then the continuous verification surface for every step).**

The web client is not a terminal milestone — it is the **window we watch the whole build
through.** Each backend piece we deploy is verified by pointing the client at it. So we build
it first (against a local daemon), host it early (static on S3/CloudFront), and keep it dialing
whatever we just stood up.

Two UIs, very different states:
- **Per-agent app UIs** (`ui/` served at `/apps/<id>/`) are **already web-native** (SDK-based,
  proven in a browser with the weather dashboard). No work.
- **The full JARVIS shell** (`clients/desktop/src/renderer`, React+Vite) already talks to the
  daemon over WebSocket (`gateway/client.ts`); only ~10 Electron IPC calls in `preload` are
  desktop-OS specific. So the web client is a **bounded port, not a rewrite**:
  - **C1. Web build target** — a plain-Vite build emitting static assets (served by the
    platform edge or the daemon).
  - **C2. Platform adapter** — replace the ~10 `window.agentd.*` IPC calls with browser
    equivalents (supervisor → no-op, daemon is always up; file save → download, open → upload;
    open-window → tab).
  - **C3. Configurable endpoint + auth** — the client dials a chosen backend (URL + token):
    `ws://localhost` + local token for dev, `wss://our-host` + account token in the cloud.
    THIS is what makes it the universal test surface. The localhost client IS the production
    client, minus the address it dials — nothing is throwaway.

---

## 8. Milestones — client-first, then SEEN at every step

The web client is built first and becomes the verification surface: after each step we point
it at what we just stood up and watch it work.

| Milestone | Steps (in order) | What you SEE in the web client |
|---|---|---|
| **M0 — Web client on localhost** | Track C (C1–C3) | Full JARVIS in a browser, dialing the local daemon; your real agents, chat, tools, sessions all work. Baseline. |
| **M1 — Platform keys + accounts** | Model Proxy (B1) · model seam → proxy (A5) · Accounts + login (B2) | Point the client's backend at the proxy → a chat reply from OUR keys, metered. Then a real sign-in screen; the same chat works under an account. Desktop app gets this too. |
| **M2 — Multi-user engine core** | per-user state root (A1) + identity at gate (A2) · web-mode confinement (A3) | Log in as two users → each sees only their own agents/sessions. (Behavior is visible; the isolation GUARANTEE also gets an adversarial test — A cannot read B's root.) |
| **M3 — Browser product on the cloud** | Hosted Runtime infra (B4) · host the client on S3/CloudFront | Open the client's CLOUD URL dialing `wss://our-host` → JARVIS running on the cloud engine, not the laptop. |
| **M4 — Marketplace & sharing** | Registry/Platform API (B3) | Install/share an agent from the catalog and watch it appear; the 5-second share works, one catalog for web + desktop. |
| **M5 — Custom-plugin agents on web** | executor seam (A4) + sandbox plane (B5) | Run a custom-plugin agent on the web and watch it execute. A red-team probe proves the sandbox can't reach a neighbour, the core, or our keys. |
| **Later — Billing** | Stripe on B1's metering | Usage converts to money. |

Two guarantees can't be confirmed by eyeballing the client and get automated tests alongside
the visual check: **isolation** (adversarial A-cannot-read-B) and **sandbox escape** (red-team
probe). Terraform is written **last, per milestone** — it transcribes an agreed design.

---

## 9. Immediately next

1. Review + correct this document and the diagrams until they match intent exactly.
2. **M0 — the web client on localhost (Track C).** Add the Vite web build target, the ~10 IPC
   shims, and a configurable backend endpoint; run JARVIS in a browser against the running local
   daemon. This is the walking skeleton and the verification surface for everything after it,
   and it needs no cloud, no refactor, no accounts.
3. Then each cloud milestone is deployed AND immediately watched through this same client
   (§8), so progress is visible at every step.

---

## 10. Open decisions (not yet locked)

- Compute shape for the fleet/gateway: ECS Fargate vs App Runner (idle cost vs control).
  True scale-to-zero is impossible (WebSockets + cron need a warm instance).
- Sandbox substrate: ECS Fargate tasks (Firecracker under the hood) vs a dedicated provider
  (E2B/Modal) vs self-managed Firecracker.
- Anonymous "try before sign-in" surface: reuse the existing `[app] public_tools` mechanism
  as the agent-declared free slice.
- BYOK-on-web storage (per-user key vault) — deferred past Phase 2.

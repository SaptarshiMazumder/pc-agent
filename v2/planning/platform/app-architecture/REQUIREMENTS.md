# pc-agent Platform — Requirements & Architecture (v2 understanding)

*Status: requirements clarification. Not a build plan. Captures decisions made so far + open items.*

## Goal

Turn **agentd (v2)** from a single-user local agent into a **platform**: a personal agent each
user runs **locally** (so it can act on their own PC — files, apps, browser), reachable from
**many front-ends**, with **flexible LLM placement**, centrally managed for **accounts and
monitoring** (billing later). Every layer must be **independently swappable**.

## Decisions made (this session)

| Question | Decision |
|---|---|
| Deployment topology | **Local app + central control plane** (+ optional shared LLM VM) |
| LLM placement | **All three tiers**: local model / BYOK / shared GPU VM |
| Front-ends | **All**: local web UI, desktop app, terminal, messaging channels |
| Billing | **Deferred** — build accounts + usage monitoring now; record usage so billing bolts on later |
| Authentication | **Mandatory** — user must register/login before using the app; one central account verified across all devices |
| Data sovereignty | Memory/history/inference **never touch a third-party AI vendor** in the private tiers; user picks where their data lives (our secure servers, their own, or local) |
| Encryption | **End-to-end** — the memory/history Vault is client-side encrypted; the server stores **ciphertext only** and cannot read it |
| Memory | **First-class pluggable subsystem** (not just chat) behind one interface; canonical in the cloud, local sync/cache; **swappable to any backend**; build local-first, swap to a cloud memory bank later |
| Architecture style | **Clean / Hexagonal (ports & adapters)** is the backbone; agent = hexagonal core + event-driven loop; control plane = layered-clean web service (the only "MVC-ish" part); front-ends = component SPA; start as a **modular monolith** |
| Desktop delivery | A **native shell (Tauri or Electron)** renders the web UI and **spawns the bundled Python `agentd` as a sidecar**; shipped as a signed installer (.exe/.msi/.dmg). Packaging only — no architecture change |
| Transport / exposure | agentd is a **local server** for the UI (`127.0.0.1`, token-gated) **and an outbound client** to the control plane + LLM; nothing on the user's machine is exposed to the internet |
| Agent engine | **Swappable** — the reason→act loop is a port. **Current/default = our native loop** (LiteLLM, provider-agnostic). Claude Agent SDK / LangGraph are pluggable alternatives (Claude SDK locks that session to Claude) |
| Security / guardrails | **Action policy at one chokepoint** — every tool call is authorized (allow / deny / needs-approval) *before* it runs; sensitive/irreversible actions (send email, destructive exec, spend) require **human approval**. Distinct from account auth |

## The three components

1. **Local Agent App** — agentd, installed on each user's machine. The agent loop and **all tools
   run here**, so `exec`/`read`/`browser` act on *their* PC (fast, private). It connects **out** to:
   (a) an **LLM endpoint** (one of the three tiers), and (b) the **control plane** (for auth + usage
   reporting, and to receive relayed messages). It holds a persistent outbound connection to the
   control plane so the user's machine doesn't need a public address.

2. **Control Plane** (your cloud) — the net-new piece. Responsibilities:
   - **Accounts & auth** (mandatory login; short-lived signed tokens the local app presents)
   - **Entitlements** (tier, quota, which Vault/LLM the user may use)
   - **Usage monitoring + admin dashboard** (per-user usage, status — metadata only)
   - **Encrypted Vault sync** (stores **ciphertext only** — see Data Sovereignty below)
   - **Message relay** for push-style channels (see Ingress below)
   - **Billing-ready** (usage recorded now; charging deferred)
   - It sees **metadata + ciphertext only** — never plaintext content or the agent's work.

3. **Shared Secure LLM Server** (your GPU VM, the private managed tier) — open-weight models
   (Kimi/Qwen/Llama) behind an **OpenAI-compatible endpoint**, token-gated by the control plane,
   **stateless and non-logging** (processes prompts in RAM, retains nothing). No third-party vendor
   in the path.

4. **Memory subsystem** — a separate, **pluggable** module (see "Memory subsystem" below) holding far
   more than chat: history, user profile, learned memories, and a retrieval index. Behind one
   interface, swappable to any backend (local now → cloud memory bank later → vector store → their
   server). Canonical copy in the cloud (cross-device), local sync/cache; **end-to-end encrypted**
   when stored on our servers.

## What runs where — the key privacy/security property

This is the most important consequence of the local-app choice:

- **Agent work + tools + files → the user's own machine.** Your servers **never execute user
  code.** → The hardest problem in hosted agents (per-user sandboxing of `exec`/`browser`)
  **does not exist here.**
- **Control plane → identity, usage metadata, message relay, encrypted Vault sync.** No plaintext
  content, no agent execution.

Cost mirrors this: BYOK/local tiers cost you ~nothing (just control-plane hosting); only the
shared-VM tier has real (flat, per-hour) GPU cost — and that's the tier you'll meter/charge for.

---

# Data Sovereignty, Privacy & Security

This is a core product pillar, not an afterthought: in the private tiers, **no third-party AI
vendor (OpenAI, Google, Anthropic) ever receives the customer's data, in any form.**

## The non-negotiable constraint

A cloud LLM API *necessarily* sends the prompt (= the user's content) to that vendor — that is what
the API does. So **the privacy guarantee requires a self-hosted open model**; it is physically
impossible to promise "no third party sees the data" while calling OpenAI/Google. The privacy claim
and the LLM placement are the **same decision**.

"Third party" = OpenAI / Google / Anthropic / any sub-processor. **You are first-party** — the
service the user signed up for, under your privacy terms. The private tiers exclude all third-party
AI vendors.

## Mandatory authentication (gates everything)

- The app **requires register/login before it does anything** — no anonymous use.
- **One central account**, verified **across all devices** (each device logs in, gets its own
  short-lived token, same identity). The control plane is the identity source of truth.
- The same signed token gates **the LLM gateway and the Vault**, enforced **server-side** (a
  tampered local client cannot reach another user's data or the GPU without a valid signed token).

## End-to-end encrypted Memory Vault

Memory/history is a **separate, gated store** (decoupled from inference — the model never "remembers";
memory is explicitly managed by the agent). It is **end-to-end encrypted**:

- The **local app holds the key**, derived from the user's password (Argon2 → key-encryption-key
  that never leaves the device). A random data key encrypts the Vault; the server stores only the
  **wrapped** data key + **ciphertext**.
- **The server can never read stored memory/history.** Cross-device: logging in with the password on
  a new device re-derives the key and unwraps the data key → decrypts the Vault. (Recovery code
  issued at signup; lost password + lost recovery code = unrecoverable, by design.)
- **Retrieval/memory logic runs client-side** (the app decrypts locally, does the lookup); the
  server is encrypted blob storage + sync only.
- Vault location is the user's choice: **our secure servers** (E2E ciphertext) / **their own server**
  / **local-only** (single device, no sync).

## Stateless, non-logging inference

The self-hosted model is **stateless**: it processes a prompt in memory and **retains nothing** — no
logging, no storage of content. "LLM memory" and "user memory/history" are thus fully separate.

## Honest boundary of E2E (precise, not hand-waved)

E2E protects **data at rest** (the Vault — we hold only ciphertext). But a **server-side model must
see plaintext to compute**. So the guarantee per tier is:

| Tier | Inference runs on | Vault (storage) | Who can see plaintext content |
|---|---|---|---|
| **Convenience (BYOK)** | OpenAI/Gemini/Anthropic API | (any) | **that third-party vendor** — not private |
| **Managed secure** | **your** self-hosted model | E2E on your servers | only your **stateless, non-logging** model, transiently in RAM during inference; never at rest |
| **Sovereign** | the **user's own** server | their server | only them |
| **Local** | the user's machine | local | only them — plaintext never leaves the device |

So "plaintext never leaves the device" = **local / sovereign** tiers. The **managed secure** tier =
**E2E-at-rest + stateless non-logging inference** (your model sees plaintext only in RAM, only while
computing, and forgets it). Both exclude third-party AI vendors entirely.

## Data classes and where each may live

| Data | What it is | Allowed locations |
|---|---|---|
| Account / identity / entitlements | login, tier, quota | control plane (metadata only) |
| **Memory Vault** | long-term facts + retrieval store | local / our servers (E2E ciphertext) / their server — **never a third party** |
| **History / transcripts** | the conversations | same as Vault |
| **Inference prompts** | content sent to the model | wherever the model runs (private if self-hosted; exposed only in the BYOK tier) |

## Tool egress (the second data path — must be policed)

`web_search` / `web_fetch` send queries/URLs to external services — a smaller egress path than the
LLM. In the private tiers these must be **disabled or proxied/audited**, or the user told that
*search queries* (not their files) leave. The LLM hole is closed by self-hosting; tools are the
remaining surface to control.

# Memory subsystem (pluggable, typed, swappable anywhere)

Memory is a **first-class, independent module** the agent talks to through **one interface (a port)**.
Behind it, any backend can be plugged in and **removed/replaced at any time** without touching the
agent. This is the ports-and-adapters principle applied specifically to memory.

## It is more than chat history

The agent's memory holds several *types* of user data that help it improve over time:

- **Conversation history / transcripts** — per session and across sessions.
- **User profile & preferences** — durable facts about the user.
- **Learned memories** — things the agent discovers and should keep (task outcomes, key entities,
  "the user prefers X").
- **Retrieval index** — embeddings so the agent can recall *relevant* memories, not just the last chat.

## The interface (so "swap later" actually works)

The agent only ever calls a small, stable set of operations — e.g.:
`append_history` / `load_history` / `remember(item, kind)` / `recall(query, k)` /
`get_profile` / `update_profile` / `forget` / `clear`.

Adapters behind it, swappable by config:
- **LocalStore** — build this **now** (generalize v2's `SessionStore`/JSONL into this interface).
- **Cloud Memory Bank** — **later**: a server DB + vector store; the canonical, cross-device copy.
- **Vector store** / **user's own server** — other adapters, same interface.

Build plan: **local-first.** Ship the LocalStore adapter now; later "cut that part out and connect a
cloud memory bank" = write one new adapter + flip a config value. The agent doesn't change.

## Canonical in the cloud, local as sync/cache

- The **cloud copy is the source of truth** (so memory is the same on every device).
- The **local copy is a sync/cache** for speed and offline use.
- Only what *must* be local for tool execution stays mandatorily local: the user's **files and
  machine** (tools act on the PC). **Memory is data, not execution** — it can live anywhere.

## How this interacts with E2E (consistent with the encryption decision)

- When the canonical copy is on **our** servers, it is **E2E ciphertext** — we store it, can't read it.
- Therefore **recall/retrieval runs client-side**: the app pulls (a working set of) memory, decrypts
  locally, and does the lookup. The cloud bank is encrypted storage + sync; the "smart recall" logic
  lives on the client.

## "Memory that improves the agent" — for whom? (decide this)

- **Per-user improvement** (the agent gets better *for that user* from *their* memory) — fully
  compatible with E2E; all decryption + personalization happens client-side.
- **Cross-user / product improvement** (analyzing many users' data to improve the model/product) —
  **E2E prevents this by design** (you can't aggregate ciphertext you can't read). If you ever want
  it, it needs explicit opt-in or separate anonymized telemetry, kept apart from the encrypted vault.

# Skills subsystem (loadable playbooks — capability without code or prompt bloat)

Skills are a **first-class, pluggable** capability layer, distinct from tools and behind their own
interface (`SkillRegistry`). A **skill is know-how, not an action**: a markdown `SKILL.md` file
(frontmatter `name`/`description` + a step-by-step body) that teaches the agent *how* to do a
specific task well.

## Skills vs tools (they are not the same thing)

- **Tool** = a callable *action* with a schema (`read`, `exec`, `browser`). Its schema is always in
  context; calling it *does* something.
- **Skill** = a *playbook* the agent *reads*. It adds **knowledge**, not a new action — e.g. the
  `browser-automation` skill doesn't add a tool, it teaches the snapshot→act→scroll→wait loop for the
  browser tool the agent already has.

## How it works (progressive disclosure)

The prompt advertises only each skill's **one-line description** (cheap, scales to many skills). When
a request matches, the agent reads that skill's **full body on demand using the ordinary `read`
tool**, and follows it. So skills need **no new tool, no core change, and no prompt bloat** — adding
one is dropping a `SKILL.md` into the `skills/` folder; it's picked up on the next message.

## Why it's worth having

- **Teach domain workflows with no code** — a browser routine, "export from Photoshop", "fill this
  expense form", "how we apply to jobs" — authored as markdown by anyone, not hardcoded in Python.
- **No prompt bloat / no hardcoding** — the long tail of "when X, do it this way" lives in files that
  load only when relevant, instead of bloating the system prompt or branching in code.
- Same pattern as OpenClaw's skills and Anthropic's Agent Skills.

## Swappable backend (port + adapters)

- **FileSkills** — build this **now**: scans `skills/<name>/SKILL.md` on the local machine. (Done.)
- **Cloud / per-user skill vault** — **later**: skills synced per account (and, like memory, E2E if
  stored on our servers); same `SkillRegistry` interface, swap by config.
- Skills are **local content on the user's machine** (like tools and files) — fully inside the trusted
  device boundary; nothing about a skill leaves the device.

# Security, Auth & Guardrails

Two *different* concerns get called "security"; they bind at different places:

1. **Auth / identity** — *who are you, what plan?* (login, token, entitlements). Gates app access
   and tier. This is the **Identity/Auth port** (control-plane).
2. **Action policy / guardrails** — *should the agent be allowed to do THIS action?* (write, exec,
   send email…). Governs the agent's autonomous actions. This is a **separate Policy port**.

## The chokepoint (where action-security binds)

Every tool call passes through **exactly one place**: the moment between "the model decided to call a
tool" and "the tool runs" — right before `tool.execute()` in the loop. **All action-security binds
there** (OpenClaw's `beforeToolCall` hook + exec approvals; cut in v2, but the seam exists).

A **Policy port** is consulted before any tool runs:

```
decision = policy.authorize(tool, args, ctx)   # ctx carries the user's entitlements (from the token)
  ALLOW          -> tool.execute(...)
  DENY(reason)   -> return an error result to the model ("blocked: ...")
  NEEDS_APPROVAL -> ask the human via the Approval port; run only if approved
```

- **Approval port** = a human-in-the-loop callback: the loop emits an "approval request" event, the UI
  shows e.g. "Allow the agent to send this email? [Yes/No]", and the loop waits.
- **Wiring (core stays untouched):** wrap every tool in a `GuardedTool(realTool, policy, approvals)`
  in the **composition root**. Policy is just another adapter layered on the Tools port; the core
  never knows about it. Entitlements flow from the **auth token → policy context**.

## The full security stack (what binds where)

| Layer | Question | Where it binds |
|---|---|---|
| **AuthN (identity)** | logged in? | ingress / login gate + token |
| **AuthZ (entitlements)** | what tier/plan? | token claims → feed policy |
| **Capability gating** | is this tool enabled for this user/session? | Policy (tool allowlist per tier — like OpenClaw profiles `all`/`read-only`/`messages`) |
| **Per-action approval** | sensitive/irreversible? | Policy → `NEEDS_APPROVAL` → Approval port (human) |
| **Argument-level checks** | write outside allowed paths? email a stranger? SSRF? | Policy inspects `args` (path/recipient allowlist, egress rules) |
| **Rate limits / quotas** | too many calls / too much spend? | Policy + Metering |
| **Content / safety filters** | disallowed content? | Policy |
| **Data-egress policy** | private tier — can web tools send data out? | Policy on `web_search`/`web_fetch` |
| **Transport** | TLS, localhost token | transport layer (have it) |

## Mapped to concrete actions

- **read** → usually allow; deny secret paths (`.env`, key files).
- **write / edit / exec** → allow inside the workspace; **needs-approval** for writes outside it or
  destructive `exec` (rm, format, sudo).
- **send message / email** → outbound + irreversible → **needs-approval** (confirm recipient + content)
  or recipient allowlist. The classic "agent about to email the wrong person" guard.
- **spend money / post publicly** → always **needs-approval**.

## Ingress — all front-ends via ONE adapter layer

The core must stay a neutral **"message in → stream out"** engine (agentd's `chat.send` already is
this). Each front-end is an **adapter** that translates to/from that core — add a front-end without
touching the core (the OpenClaw channel-adapter pattern, which we have a verified blueprint for).

- **Local web UI** (browser → `localhost`) — recommended first; familiar, nothing extra to install.
- **Desktop app** — packaged window (native/Electron).
- **Terminal** — already built.
- **Messaging (WhatsApp/Telegram/Line/…)** — real constraint to design around:
  - The local app is only online when the user's machine is on.
  - **Pull-style** platforms (e.g. Telegram long-polling) the local app can connect to directly.
  - **Push-style** platforms (WhatsApp Cloud, Slack events) deliver via webhook to a public URL —
    a local app has none, so the **control plane relays**: platform → control-plane webhook →
    forwards down the app's persistent connection. This is why the control plane has a relay role.

## LLM plane — three tiers, one config shape

Every model is just **`provider + baseUrl + apiKey`** (already true via LiteLLM in v2). The tier is
which endpoint that points at:
- **Local** — Ollama / local OpenAI-compatible server on the user's machine (free, private, smaller models).
- **BYOK** — the user's own OpenAI/Gemini/Anthropic key (they pay per token).
- **Shared VM** — your hosted open model, token-gated by the control plane (managed tier).

Swappable per user/tier; the loop doesn't care which.

## Swappable modules (ports & adapters)

"Replace any module" = define a stable interface (port) per responsibility; any implementation
(adapter) drops in behind it. Ports:

| Port | Swappable thing | In v2 today |
|---|---|---|
| **Agent engine** | the reason→act loop itself (native / Claude SDK / LangGraph) | **native loop** (`loop.py`) — make it a port |
| Ingress (channel) | front-ends | terminal only — needs the adapter layer |
| Inference (stream_fn) | LLM tier (local / BYOK / secure VM) | ✅ pluggable (LiteLLM) |
| Tools (registry) | capabilities | ✅ pluggable |
| **Skills** | loadable SKILL.md playbooks (know-how, read on demand) | ✅ FileSkillRegistry; ships `browser-automation` |
| **Policy / guardrails** | authorize each tool call (allow/deny/needs-approval) at the pre-execute chokepoint | seams exist; logic cut |
| **Approval (human-in-the-loop)** | confirm sensitive/irreversible actions via the UI | none — net-new |
| Identity / auth | who/tenant (**mandatory login**) | none — net-new |
| **Memory subsystem** | typed memory backend: LocalStore / cloud memory bank / vector store / their server | basic JSONL (`SessionStore`) today; generalize into the Memory interface, then add cloud bank |
| Crypto / keys | client-side key derivation + wrapping | none — net-new |
| Metering (usage sink) | usage events → control plane | none — net-new |

# Architecture style (what pattern for which section)

There is **no single pattern for the whole project** — it's a few services, each with the right
pattern, with **Clean / Hexagonal (ports & adapters) as the backbone** wherever there is real logic.
MVC is **not** the model for the agent; it appears only (as layered-clean) inside the control-plane
web service.

| Section | Pattern | Notes |
|---|---|---|
| **Agent core** (the loop) | **Hexagonal/Clean + event-driven loop** | stateful & streaming, not request→response; already shaped in v2 |
| **Ports & adapters** (LLM, tools, memory, transport, ingress) | **Ports & Adapters** | delivers the "replace any module" requirement |
| **Control plane** (auth, entitlements, usage, vault sync, relay) | **Modular monolith**, **layered clean** (API → use-cases → repositories), REST + WebSocket | the only "MVC-ish" part; split into services only when scale forces it |
| **Memory subsystem** | **Port + adapters**; cloud bank = storage/repository service | local now, cloud later |
| **LLM gateway** | **API gateway / proxy** (auth → quota → meter → forward) | protects the GPU |
| **Model server** | off-the-shelf (**vLLM / SGLang**) | run it, don't write it |
| **Front-ends** (web UI, desktop, admin) | **Component-based SPA** (React/Vue, frontend MVVM) | thin clients over the API/WebSocket |
| **Channels** (Telegram/WhatsApp/…) | **Ingress adapters** | one per platform |

**Concrete layout of the two things you write:**

Agent app (hexagonal) — `core/` (loop, domain, no IO) · `ports/` (Inference, Tools, Memory,
Transport, Ingress, Auth, Metering) · `adapters/` (llm, tools, memory, transport) · `app/` (wiring).
Rule: `core/` and `ports/` import nothing from `adapters/` — dependencies point inward.

Control plane (layered clean) — `api/` (HTTP/WS routes = thin controllers) · `usecases/` (auth,
entitlements, usage = the real logic) · `domain/` (User, Account, Entitlement, UsageRecord) ·
`repositories/` (DB + ciphertext store, behind interfaces). Routes → use-cases → repositories.

Guidance: **build the control plane as a modular monolith first**, not microservices.

## Swappable agent engine (the brain is a port too)

The **agent engine itself is swappable** — the reason→act loop is not hardwired into the app; it sits
behind an `AgentEngine` port. Adapters:

- **Native engine — CURRENT DEFAULT.** Our hand-rolled loop ([loop.py](../../../agentd/loop.py)) + the
  OpenClaw-faithful continuation/verification protocol ([incomplete_turn.py](../../../agentd/incomplete_turn.py)),
  driven by **LiteLLM**. **Provider-agnostic** → works with all three LLM tiers. This is what v2 runs today.
- **Claude Agent SDK** — a pluggable option, **but Claude-only**: it bundles Anthropic's provider and
  bypasses our Inference port, so that session **cannot** use the BYOK / local / secure-VM tiers.
  Keep it as an *option*, never the default. (We rejected it as the base for exactly this reason —
  it breaks the any-LLM requirement.)
- **LangGraph** — provider-agnostic graph engine (uses LiteLLM); an alternative control-flow style.

**What stays ours regardless of engine:** tools, memory, and event streaming are **ports**. The engine
adapter only *translates* — our `Tool`s → that SDK's tool format (and its tool-calls back to our
`execute()`), its events → our `EventSink`, and history in/out of our `MemoryPort` (so the E2E memory
bank stays canonical). So swapping the brain doesn't touch tools, memory, transport, or the UI.

**Caveats:** (1) the engine *is* the behavior — swapping it changes results, not just plumbing (the
native engine's continuation/verify protocol is ours, not universal); (2) each engine pulls its own
deps (`anthropic`, `langgraph`) — keep them isolated in their adapter so the core never depends on them.

# Desktop app — packaging & distribution

Shipping a desktop GUI **does not change the architecture** — it is packaging. The GUI is just
another **client** over agentd's local gateway (the terminal client already proves the split).

**Sidecar pattern (recommended):**
- Build the **UI once as a web app** (React/Vue) — it is *both* the local web front-end and the
  content shown in the desktop window.
- A **native shell** (**Tauri** = small/secure via OS webview, or **Electron** = mature/JS-heavy)
  renders that web UI **and spawns the bundled Python `agentd` backend as a child process
  ("sidecar")**, talking to it over the local WebSocket already built.

**Packaging pipeline:**
1. **Freeze the Python backend** → a single executable with **PyInstaller** (or **Nuitka**); handle
   **Playwright's Chromium** (bundle or first-run download).
2. **Wrap in the shell** (`electron-builder` / Tauri) → per-OS installers: Windows **.exe/.msi**
   (NSIS), macOS **.dmg**, Linux **AppImage/.deb**.
3. **Code-sign + notarize** — Windows Authenticode (SmartScreen), Apple Developer ID + notarization
   (Gatekeeper). Required, or users see "unknown publisher" warnings.
4. **Auto-update** — electron-updater / Tauri updater pulling from your release server.

**Caveats:**
- **Install size is large (hundreds of MB)** — mostly Chromium (for the `browser` tool) + the Python
  runtime; true regardless of shell choice.
- **Code-signing costs money + setup** (annual Windows cert; Apple Developer $99/yr).
- **Local-LLM tier needs a local runtime** (e.g. **Ollama**) — separate install or bundled.
- **Cross-platform = separate builds + signing per OS.**
- **Sidecar lifecycle** — the shell spawns/stops/restarts agentd and discovers its port.

**Decision fork:** keep **Python backend + JS/Tauri shell (sidecar)** — pragmatic, reuses everything,
*recommended now* — vs **rewrite the agent in TypeScript** to unify into one language (cleaner desktop
story, but a big rewrite; this is why OpenClaw is TS). Default: sidecar.

# Transport & exposure (how agentd is reached)

agentd has **two roles in opposite directions**:

```
   UI  --connects in-->  agentd (LOCAL SERVER on 127.0.0.1)
                              |
                              +--dials OUT-->  Control plane (cloud)
                              +--dials OUT-->  LLM endpoint
```

**To the UI — a localhost server (have this in v2):**
- agentd **binds a WebSocket (+ small HTTP) on `127.0.0.1:<port>`**, **localhost-only** (never
  `0.0.0.0`). The UI speaks the existing `chat.send` / `chat.event` protocol.
- **Port handshake:** don't hardcode the port — the shell spawns agentd, agentd **picks a free port
  and reports it** (stdout/file), the shell points the UI at it.
- **Local security:** require a **per-launch auth token** (shell generates it, passes it to agentd
  *and* the UI). A localhost socket is reachable by any local process — even a malicious web page the
  user visits — so the token prevents anything else from driving the agent.

**To the control plane — outbound only:**
- The user's machine is behind NAT with no public address, so the server **never connects in**.
  agentd **dials an outbound persistent WSS** to the control plane, authenticated with the user's
  login token.
- Over that one connection: **usage reporting**, **encrypted Memory Bank sync (ciphertext)**,
  **push/relay** (the server pushes *down* the connection agentd opened), **entitlement** updates.
- Benefit: **no inbound firewall ports** on the user's machine — simpler and more secure.

**To the LLM — outbound client:** agentd dials out to the chosen endpoint (local Ollama / BYOK vendor
/ your token-gated gateway).

**Login placement:** either the UI talks to the control plane **directly** for login/account (then
hands the token to agentd), or **all account calls go through agentd**. Default: UI does login
directly; agentd handles agent traffic.

**v2 status:** the localhost WebSocket gateway exists ([gateway.py](../../../agentd/gateway.py)). To add:
free-port + token handshake (for the UI) and the **outbound control-plane client** (for the server).

## Reuse vs net-new

- **Reuse / proven:** agent core (have), tools (have), **skills** (have — FileSkillRegistry +
  `browser-automation`), LLM tiers (have via LiteLLM), channel-adapter pattern (OpenClaw blueprint).
- **Net-new:** the **control plane** (accounts, mandatory auth, entitlements, monitoring, relay,
  encrypted Vault sync), the **front-ends** (web UI, desktop), the **desktop shell + sidecar
  packaging** (Tauri/Electron + frozen Python + installers/signing/auto-update), the **local
  server hardening** (free-port + token handshake), the **outbound control-plane client**, the
  **E2E crypto layer** (client-side key derivation/wrapping), the **guardrail layer** (Policy port for
  per-tool-call authorization + Approval port for human-in-the-loop), and the **stateless secure LLM gateway**.

## Deferred / open (sensible defaults noted)

- **Billing specifics** — deferred; but record per-user usage now so it's ready.
- **Auth mechanism (DECIDED: mandatory)** — register/login required; password → client-side key
  (Argon2); short-lived signed token gates control plane + Vault + secure LLM. Remaining detail:
  exact token scheme (JWT/refresh) and recovery-code UX.
- **Control-plane data model** — users, devices, entitlements, usage records, **wrapped data keys +
  Vault ciphertext** (never plaintext).
- **Isolation** — control-plane data multi-tenant (pooled + `tenant_id`); **compute already
  isolated** (each user's own machine); secure LLM = per-request auth + rate limit; Vault =
  per-user E2E ciphertext.
- **Crypto specifics** — key-derivation params, key rotation, recovery-code flow — TBD.
- **Which messaging channels first**, and **desktop packaging tech** — TBD.

## Rough sequencing (not a detailed plan)

1. **Refactor v2 into clean core + ports + adapters** — formalize the hexagon (mostly there).
2. **Auth-gated local app core + local web UI** — register/login required; localhost server with
   free-port + token handshake; usable on local/BYOK LLM.
3. **Memory interface + LocalStore adapter** — route all the agent's memory (history, profile,
   learned, retrieval) through the one interface; local-first.
4. **Desktop shell + sidecar packaging** — Tauri/Electron wraps the web UI + spawns frozen agentd;
   signed installers (.exe/.msi/.dmg) + auto-update.
5. **Control plane MVP (modular monolith)** — accounts + auth + signed tokens + entitlements + usage
   + admin; the **outbound control-plane client** in agentd.
6. **Cloud Memory Bank adapter + E2E + cross-device sync** — swap LocalStore for the cloud bank by
   config; client-side keys; server stores ciphertext; canonical cloud + local cache.
7. **Shared Secure LLM VM** — stateless, non-logging, token-gated (the private managed tier).
8. **Messaging channels** via the relay; **tool-egress policy** for private tiers.
9. **Billing** (when ready) on top of the usage already being recorded.

## Honest caveats (not hallucinated — flag these)

- "All front-ends" is a lot of surface area; realistically build them in sequence (web UI first),
  not at once.
- Messaging via a local app is genuinely constrained (online-only; push channels need the relay).
- **E2E has a hard limit:** it protects data *at rest*; a server-side model must see plaintext to
  compute. "Plaintext never leaves the device" = local/sovereign tiers only. Market this precisely.
- **E2E means true zero-knowledge:** if we can't read the Vault, we can't recover it — lost password
  + lost recovery code = lost data. This is a feature (privacy) and a support burden (be explicit).
- **Tool egress** (`web_search`/`web_fetch`) is a second data path; must be disabled/proxied in
  private tiers or the leak (search queries) disclosed.
- A self-hosted open model has **no per-token fee but real, continuous GPU cost** — budget for the
  server, not per request.

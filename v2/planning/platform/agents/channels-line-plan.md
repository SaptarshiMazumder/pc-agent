# Channels — LINE: customer bot + owner control (execution plan)

> **Goal.** Two audiences, one unchanged core: (1) **customers** message a restaurant's **LINE
> Official Account** and a **constrained restaurant agent** answers (FAQ + reservations,
> escalating to the owner); (2) the **owner** messages a separate channel to **operate their
> full agent** (commands, session switching). Both are expressed as **config + agent
> definitions** — the core agent runtime never changes.
>
> **Method.** Same discipline as `parity-execution-plan.md`: **one step per turn**, each
> respects the import-linter layering (main > presentation > infrastructure > application >
> domain), is independently testable, ends with `pytest tests/` + `lint-imports` green.
> Default-OFF / additive — nothing starts until a channel is configured.
>
> **OpenClaw refs** (`reference/openclaw-main/extensions/line/src/`): `signature.ts` (HMAC),
> `monitor.ts` + `webhook-node.ts` (ingress + ack), `bot-handlers.ts` + `bot-message-context.ts`
> (normalize), `send.ts` + `reply-chunks.ts` (**reply-token-first then push**), `accounts.ts` +
> `config-schema.ts` (creds + `dmPolicy`). Core: `src/channels/plugins/types.plugin.ts`.
>
> Status: ☐ todo · ◐ in progress · ☑ done

---

## Architecture invariant — external requirements live at the edge

The core runtime never learns what a "customer" or a "restaurant" is. It only ever **runs
agent X on session Y with tool-scope Z.** *Who* may trigger that and *which* agent answers is
decided at the edges. Every external requirement lands in one of three layers; only the bottom
one grows per requirement:

| Layer | Changes per new channel/role/tenant? | What lives here |
|---|---|---|
| **Core runtime** — domain · application ports · engine loop · `AgentService` · session model · `Channel` port · tool framework · notify · gateway run/dispatch | **Never** | the agent: run X on session Y with tools Z |
| **Edge adapters** — channel adapters behind the port · webhook driver · factory registry · command router | **Once per brand-new platform** (additive, localized) | how bytes enter/leave a platform |
| **Composition** — `config.channels[]` · `agents/<id>/` dirs · `policy` values · tool allowlists | **Every channel/role/tenant = config + data only** | which agent answers which audience under which policy |

**Owner-vs-customer is purely the bottom row** — two channel bindings to two agents. Two
channels (not role-routing on one) is deliberate: routing two audiences through one channel
would push "owner or customer?" logic *into* the core run path. Separate bindings keep each
audience's policy + agent + tool-scope declarative and isolated. See `agent-channels-composition.puml`.

## Platform code vs. tenant content — what we do NOT build

The word "restaurant" must appear **nowhere** in the platform code. We ship generic
primitives; a restaurant is *assembled* from them at deploy time.

| We build (generic platform code, `v2/agentd/`) | NOT built — tenant content (config + data) |
|---|---|
| `LineChannel` (generic LINE transport), `WebhookServer`, `policy` gate, session router, command router | The **`restaurant` agent** = a directory `agents/restaurant/` (`agent.toml` + `IDENTITY.md`) — like the existing `cost-calc`/`expense-calc` agent dirs |
| MCP support + notify (already exist) | The **menu/hours/persona** = text in `IDENTITY.md`; the **owner setup** = `config.channels[]` entries |
| — | **Reservations** = an MCP server (Sheets/Calendar/custom) the agent is pointed at, allow-listed in `agent.toml` — **not** a core tool |

The only platform code is **L1–L3 + O1–O2** (generic channel infra, reusable for any LINE bot).
The **R-tier is not code** — it's "how to assemble a restaurant from config + a directory + an
MCP" (example/runbook). A constrained agent records reservations by calling its reservation MCP;
it never gets shell/fs.

## Use case (locked)

- **Both audiences, separate channels.**
  - **Customers** → restaurant OA → `restaurant` agent (constrained) → `policy: open`, no commands.
  - **Owner** → a separate channel → `main` agent (full power) → `policy: allowlist`, commands on.
    (Owner channel may be a 2nd LINE OA, Telegram — no tunnel needed — or just the terminal.)
- **Restaurant scope: info + reservations/orders**, with **human handoff** to the owner.

## Design decisions (locked)

- **Channel = built-in adapter** behind the existing `Channel` port + one factory case (no
  plugin loader; see `channels-adapter-vs-plugin` rationale).
- **Push transport, webhook-only.** LINE has no polling. A `WebhookServer` driver receives
  events and fires them through the *existing* gateway channel path; `LineChannel.poll()` → `[]`.
- **Per-customer / per-owner thread is free.** `_fire_channel` already routes to
  `agent:<id>:line:<peer>`, so each `userId` gets its own persistent conversation.
- **Policy is a per-channel value:** `open` (answer everyone — customer OA) | `allowlist` (owner
  channel). The webhook **signature is always verified** regardless (authenticity of LINE).
- **Commands are a per-channel capability** (`commands: true`), gated to `allowlist` channels — so
  customers never see `/new`/`/resume`; the owner does.
- **Outbound = reply-token-first, push fallback.** Reply-token replies are **free**; push counts
  against LINE's monthly quota. Reply via the event's `replyToken` when fresh (<~60s), else push.
- **Dedicated, constrained `restaurant` agent.** A public channel must **never** reach `main`
  (shell / computer-use / fs + prompt-injection). The bot's `agent.toml [tools] allow` is the
  chokepoint: knowledge + a reservation **MCP** tool + handoff only.
- **Reservations via MCP/skill, not core code.** Recording bookings is a tenant capability
  (Sheets/Calendar/custom MCP the agent is pointed at) — the platform ships no `create_reservation`.
- **Reachability:** tunnel (cloudflared) for dev; a customer-facing OA in production belongs on an
  always-on cloud host / relay over the existing WS — not the owner's PC.

## What is reused unchanged (the "core never changes" list)

`ChannelPoller` · `_fire_channel` / `_run_channel` · `AgentService.handle_message` (mode=CHANNEL) ·
engine loop + transcripts · `_last_answer` · per-agent **tool scoping** (`agent.toml [tools]`) ·
session model · the **notify** system · `ChannelNotifier`. The only one-time edge additions are
L1–L3 + O1–O2 below; after that, new channels/roles/tenants are **config + a directory**.

---

## Tier T — Transport (pure logic; no LINE account needed)

- ☑ **L1. `LineChannel` adapter.** *Done (11 tests; channel suite 20 green; import-linter kept).*
  `infrastructure/channels/line_channel.py` (implements `Channel`).
  `verify(body, sig)` (base64 HMAC-SHA256); `parse_events(body)` → `InboundMessage` list (text only
  MVP; `peer` = userId; `external_id` = message.id; **stashes `replyToken`+ts per peer**); `policy`
  = `open` (default) | `allowlist` (drops senders not in `allow_from`); `poll()` → `[]`; `send(peer,
  text)` → **reply API if fresh token else push** (`httpx`, chunk 5000, ≤5/call). Tests: HMAC, normalize,
  policy open vs allowlist, token-vs-push selection. *Layers: infrastructure (+ domain `InboundMessage`).*

- ☑ **L2. `WebhookServer` driver.** *Done (7 tests incl. e2e signed POST; lint kept).*
  `infrastructure/channels/webhook.py` (aiohttp). Routes
  `POST <webhook_path>` per push-channel; `verify` → 401; **ack 200**; dedup `external_id`;
  `asyncio.create_task(fire(channel, msg))`. Modeled on `ChannelPoller`; started in `serve()` with
  `_fire_channel`. Add `aiohttp` to `requirements.txt`. Tests: signed POST → fires; bad sig → 401;
  dup id → no re-fire; **two channels on two paths** route independently. *Layers: infrastructure.*

## Tier W — Wiring (multi-channel)

- ☑ **L3. Factory + config + `serve()` wiring.** *Done (factory `line` case + env-suffix
  multi-account; `webhook_host/port` config + env; `_start_webhook_server` in serve(); 378
  passed, lint kept). aiohttp added to requirements.* `build_channel`: `line` case (creds from
  `LINE_CHANNEL_SECRET[_<suffix>]` / `..._ACCESS_TOKEN[_<suffix>]` env so **multiple LINE channels**
  coexist; config override; `policy`, `allow_from`, `webhook_path`, `agent`, `commands`). `config.py`:
  `webhook_host`/`webhook_port`. Gateway: `_start_webhook_server()` for channels exposing
  `webhook_path` (one server, many paths). Full suite + lint green. *Layers: infrastructure + presentation.*

## Tier R — Assemble a restaurant (CONFIG + DATA, not platform code)

> Not build steps for the codebase — this is the operator runbook for standing up one tenant
> from the generic primitives. The only thing that could touch `v2/agentd/` here is a *generic*
> notify/handoff tool **if** one doesn't already exist (and it's generic, never "restaurant").

- ☐ **R1. `restaurant` agent directory** (data). `agents/restaurant/agent.toml` + `IDENTITY.md`,
  exactly like `cost-calc`. `[tools] allow = [<reservation MCP tool names>]` (NO exec/computer/
  read/write/edit/ls/find/process); `[capabilities] notify = true, channels = true`. `IDENTITY.md`
  = persona + menu/hours/location/policy. Bound via `{"agent":"restaurant"}`. (Verify: registry
  loads it; dangerous tools absent — uses existing `FileAgentRegistry`, no new code.)

- ☐ **R2. Reservation capability via MCP** (config). Point the agent at a Sheets/Calendar/custom
  **MCP server** (same mechanism as the Gmail MCP already used by other agents) and allow-list its
  tool(s) in `agent.toml`. The agent records bookings by calling that MCP — **no `create_reservation`
  code in our repo**, core stays generic. (Alt: a SKILL.md that drives an already-allowed tool.)

- ☐ **R3. Human handoff** (reuse generic notify). The agent escalates to the owner via the existing
  notify primitive when unsure; customer gets “passed to our team.” Only platform work: a small
  *generic* `notify_owner`/handoff tool **iff** no agent-callable notify exists yet — not restaurant-specific.

## Tier O — Owner control (the operate-the-agent track; gated to allowlist channels)

- ☐ **O1. `ChannelSessionRouter`.** `infrastructure/channels/session_router.py` — persistent
  `(channel, peer) -> active session_key`; `active()` (default `agent:<id>:line:<peer>`), `new()`
  (branch), `set()` (resume), caches last `/sessions` listing per peer for `/resume <n>`. Tests:
  default/branch/switch/reload. *Layers: infrastructure.*

- ☐ **O2. Command router (allowlist channels only).** A pre-run component the gateway calls: on a
  channel with `commands: true`, leading `/` → handle via the session router and reply **without** a
  run; else resolve active session + run. Commands: `/new [title]`, `/sessions`, `/resume <n>`,
  `/here`, `/help`. On `open` channels, commands are ignored (customers just chat). `_fire_channel`
  consults the router for command-enabled channels. Tests: command round-trips on an allowlist
  channel; ignored on an open channel; plain text routes to active session. *Layers: presentation +
  infrastructure.*

## Tier Live

- ☐ **L4. Live smoke + runbook.** Restaurant OA: Messaging API channel → secret + token → `v2/.env`;
  OA Manager → Response mode **Bot**, Webhook **on**, disable Auto-response/Greeting. Owner channel:
  a 2nd LINE OA (or Telegram, or terminal). `cloudflared tunnel --url http://localhost:8788`;
  set each Webhook URL to its `webhook_path`. Customer DM → restaurant agent replies + reservation →
  owner notified. Owner DM → full agent + `/sessions`/`/new` work. Capture the runbook here.

## Tier H — Hardening / later (backlog)

- ☐ **L5.** Durable dedup (sqlite) · **pairing** for restricted channels (deferred **S13**) ·
  streamed/progress replies · per-sender rate limit · mid-run message queue (busy-guard drops a 2nd
  message today) · **group** chat (sender field on `InboundMessage`) · multi-OA tenancy helpers ·
  PII handling for stored customer contacts.

---

## Config — both roles, one core (`config.channels[]`)

```json
[
  {"type":"line","agent":"restaurant","policy":"open",
   "webhook_path":"/line/webhook","notify_to":"owner"},

  {"type":"line","agent":"main","policy":"allowlist","allow_from":["U_owner_id"],
   "webhook_path":"/line-owner/webhook","commands":true}
]
```
Secrets per channel via env suffix (e.g. `LINE_CHANNEL_SECRET`, `LINE_CHANNEL_SECRET_OWNER`).
Adding a third restaurant, a Telegram owner remote, or a new bot = **another entry + a directory.
No core change.**

## Safety (non-negotiable)

Public channels bind only to constrained agents; `main` is reachable only via an `allowlist`
channel (owner) or the terminal. The agent's `[tools] allow` is the chokepoint — public input
cannot reach shell/computer/fs.

## Principles (every step)
- **Decoupled:** behind the existing port / new infra modules; default-OFF.
- **SOLID:** transport · ingress · reservation · session-routing · agent-definition are separate concerns.
- **Green gate:** every step ends with `pytest tests/` + `lint-imports` green; existing paths unchanged.

# Agent System — Plan of Execution

Build **long-running, autonomous agents** on agentd: easy to create (an agent is a
*directory* of instructions + skills + data), that **think, iterate, verify, learn,
and act on their own**. Modeled on OpenClaw + Anthropic's agent stack — both converge
on the same shape, so we copy the convergence.

> Status: **DESIGN — awaiting LGTM**. No code until the design is approved.
> Diagrams: [`agent-system-architecture.puml`](agent-system-architecture.puml) (components),
> [`agent-autonomy-flow.puml`](agent-autonomy-flow.puml) (one turn / the autonomy loop).

---

## 1. The model we're copying (the "7 organs")
A long-running autonomous agent is 7 parts. OpenClaw and Anthropic build all 7; so do we.

| Organ | What it is | Phase |
|---|---|---|
| 1. Agent **definition** (vs execution) | a named, scoped, reusable config — separate from its sessions | 1 |
| 2. **Identity / bootstrap** files | persona + rules as editable markdown (IDENTITY/AGENTS/HEARTBEAT/MEMORY) | 1 |
| 3. **Skills** | drop-in `SKILL.md` playbooks (+ scripts/data), lazy-loaded | ✅ today |
| 4. **Autonomy / scheduler** | heartbeat + cron fire turns with no user | 2 |
| 5. **Goals** | objective + token budget bounding a long task | 2 |
| 6. **Memory / learning** | `MEMORY.md` + daily notes + search + skill authoring | 3 |
| 7. **Sub-agents + verification** | spawn helpers; deterministic hooks/approval | 4 |

---

## 2. Design principles (locked)
- **An agent is a directory.** Creating an agent = a folder + a small `agent.toml`. Adding
  a capability = dropping a `SKILL.md` (+ optional scripts/data) into its `skills/`.
- **Definition ≠ execution.** The *definition* (persona, scope) is durable + versionable;
  *sessions* are ephemeral runs against it.
- **Per-agent isolation.** Each agent owns its workspace, sessions, and memory namespace
  (OpenClaw forbids sharing an agent dir — we follow that).
- **The gateway is the deployable unit.** Wherever the gateway runs is where tools act —
  **local** (control my PC) or **cloud** (always-on support agent). Same code.
- **Durable structured state is separate from transcripts.** Conversations are JSONL on
  disk (today); autonomy adds a small durable ledger (SQLite) for cron/goals/runs.
- **Hooks are enforcement, not guidance.** Verification/approval ride the existing
  `GuardedTool` chokepoint — deterministic, the LLM can't skip them.

### "An agent is a directory"
```
agents/<id>/
  agent.toml      # id, model, channels, tool allow/deny, skill allowlist, heartbeat interval
  IDENTITY.md     # persona — who it is, tone, boundaries
  AGENTS.md       # operating rules / red lines
  HEARTBEAT.md    # what to check on each autonomous tick
  MEMORY.md       # long-term learned facts (agent-writable)
  skills/         # drop-in SKILL.md playbooks (+ scripts/data)  ← add capability here
  memory/         # daily notes  (agent-writable)
  workspace/      # working directory
```

---

## 3. What we already have (reuse, don't rebuild)
`read/write/edit/ls/find` · `exec/process` · `web_search`/`web_fetch`/`browser`/`computer`
· `update_plan` · **skills loader** (`infrastructure/skills/file_skills.py`) · **MCP client**
(stdio + streamable-http) · **GuardedTool** (timeout/retry — the future hook/approval seam)
· **per-session JSONL transcripts** (`SessionStore`) · **event stream** · **LiteLLM** (any model).

---

## 4. Phases (critical path 1 → 5; 6 = platform maturity)

### Phase 1 — Agent as a first-class definition  *(THE KEYSTONE)*
Make "Agent" a real object and route to it. Everything else depends on this.
- **domain:** `Agent`, `AgentId`, `AgentSpec`.
- **application:** `AgentRegistry` (port), `AgentResolver`.
- **infrastructure:** `FileAgentRegistry` (reads `agents/<id>/` + `agent.toml`); `BootstrapLoader`
  (IDENTITY/AGENTS/HEARTBEAT/MEMORY/USER → system prompt, with per-file + total caps);
  per-agent **tool allow/deny** + **skill allowlist** scoping.
- **presentation:** session-key `agent:<id>:<channel>:<peer>`; gateway resolves the agent per
  message; sessions partition to `state_dir/agents/<id>/sessions/*.jsonl`.
- **DX / back-comp:** today's single agent becomes `main` (default); zero behavior change.
- **Verify:** stand up a 2nd agent purely by adding a dir; route a message to it; it loads its
  persona + scoped tools/skills; `main` unaffected; tests + lint green.
- **Effort:** M–L.

### Phase 2 — Autonomy (scheduler + goals)
Agents run with no user.
- **domain:** `ScheduledTask`, `Goal`.
- **application:** `Scheduler` (port), `TaskStore` (port), `AutonomyService`.
- **infrastructure:** `HeartbeatScheduler` (asyncio timers, per-agent interval from `agent.toml`);
  durable `TaskStore` (**SQLite**) for cron jobs + run history (survives restart); cron expression
  parsing; `HEARTBEAT.md` loader; tools `create_goal`/`update_goal`/`get_goal`, `heartbeat_respond`.
- **presentation:** scheduler posts turns to the gateway through the *same* path a client uses.
- **Verify:** heartbeat fires a turn with no user; a cron job survives a gateway restart; a goal
  bounds a long task and self-marks blocked/done.
- **Effort:** L.

### Phase 3 — Memory & learning  *(requires un-deferring memory)*
Agents get better across runs.
- **domain:** `MemoryNote`.
- **application:** `MemoryStore` (port).
- **infrastructure:** `FileMemoryStore` (`MEMORY.md` + `memory/YYYY-MM-DD.md`); `memory_search`
  (keyword first; embeddings later) + `memory_get` tools; `MEMORY.md` injected at session start;
  `skill_workshop` tool (agent authors a `SKILL.md` into its `skills/`).
- **Verify:** a fact written in run 1 is recalled in run 2; an agent-authored skill is available
  next run.
- **Effort:** M.

### Phase 4 — Sub-agents + hooks / approval
Verify and trust risky actions.
- **domain:** `SubAgentSpec`, `Approval`.
- **application:** `Spawner` (port), `Policy` + `Approval` (ports) — the before-tool-call chokepoint.
- **infrastructure:** `SpawnRunner` (detached agent runs, parent-stream relay, depth/child caps);
  **hooks at `GuardedTool`** (pre/post tool); `ApprovalGate` (HITL for risky tools, e.g. cancel
  reservation). *Also fixes the computer-tool `safety_decision` auto-ack ToS gap.*
- **Verify:** agent spawns a sub-agent to verify a finding; a risky tool requires approval; a
  deterministic hook blocks a denied action.
- **Effort:** L.

### Phase 5 — Channels (make the use cases real)
- **domain:** channel message envelope.
- **application:** `Channel` (port), `MessageRouter`.
- **infrastructure:** adapters — email (IMAP/SMTP) first, then WhatsApp/Telegram/Slack;
  conversation-binding so replies return to the origin; unified `message` tool.
- **Verify:** an inbound email routes to the `support` agent; the reply goes back over email.
- **Effort:** L (per channel, S–M).

### Phase 6 — Platform maturity
- **Plugin/capability system** (`registerTool/registerProvider/registerChannel`) — make
  tools/search/channels pluggable + third-party. (XL; consider pulling earlier.)
- **MCP server mode** — expose agentd's tools/conversations over MCP. (M)
- **ACP / external-agent backend** — run Codex/Claude CLI as a harness + reuse its auth. (L)
- **Versioned agent definitions** + rollback; **per-session sandbox** for cloud multi-tenant.

---

## 5. Open decisions to confirm at LGTM
1. **Un-defer memory** — Phase 3 is mandatory for "learn on its own". The chosen form is
   *explicit files* (`MEMORY.md` + daily notes), cheap and inspectable — not a vector DB. **OK?**
2. **Durable ledger = SQLite** (vs JSON files) for cron/goals/runs — recommended (matches OpenClaw;
   real durability across restarts). **OK?**
3. **Deploy target is per-gateway** — local for PC-control agents, cloud for always-on agents; we
   support both, same code. **OK?**
4. **Start with a shared async gateway** (per-session concurrency) and add per-session sandboxing
   only in Phase 6. **OK?**

---

## 6. Recommended order
**Phase 1 first** (keystone), then **2 → 3 → 4** give "long-running agents that think, iterate,
verify, learn — easy to create." **Phase 5** makes the support/marketing use cases real.
**Phase 6** is platform polish. Each phase ships independently and keeps tests + import-linter green.

# OpenClaw vs. agentd — Capability Parity & Build Roadmap

> **Purpose.** A durable, evidence-based reference for deciding what to build next. It
> compares our `v2/agent_runtime` against the OpenClaw reference codebase
> (`reference/openclaw-main/`), capability by capability, with **file pointers into
> OpenClaw** so that when we build a missing piece we can study the real implementation
> first. Everything here was verified by reading both codebases (not from memory).
>
> **How to use.** Pick a gap from §5 (ranked by impact). Read "What OpenClaw does" +
> the listed OpenClaw files. Read "How we'd build it" for a design sketch that fits our
> hexagonal architecture. Estimate from the effort tag. Build, keeping the import-linter
> contract and tests green.
>
> Last verified: 2026-06-21. OpenClaw = a ~16,000-file TS monorepo, 137+ extensions.
> agentd = a focused Python port of the core (gateway + loop + tools + autonomy + the
> channels framework).

---

## 1. The two systems at a glance

| | OpenClaw (`reference/openclaw-main/`) | agentd (`v2/`) |
|---|---|---|
| Language / shape | TypeScript monorepo, `pnpm` workspaces | Python, single package |
| Size | ~16,000 source files, 137+ extensions | ~1 package, ~265 tests |
| Architecture | Core `src/` + `packages/*` + pluggable `extensions/*` | Clean hexagonal (import-linter: main > presentation > infrastructure > application > domain) |
| Maturity | Production personal-assistant ecosystem | Well-architected foundation / focused subset |
| Extensibility | Plugin SDK + extension manifests | Add a file to a layer; ports + adapters |

**Honest headline:** for *basic 1:1 chat + core tools + simple autonomy* we're **~85% on par**.
Across OpenClaw's **full capability surface** we're **~40–50%** — a clean foundation, not
feature-complete. The earlier "80–85%, only 2 gaps" estimate was too generous.

---

## 2. What agentd has today (verified inventory)

**Tools** (`agentd/infrastructure/tools/`, each wrapped in `GuardedTool`):
`update_plan, read, write, edit, ls, find, exec, process, web_search, web_fetch,
browser, computer` + autonomy tools `cron, goal, heartbeat_respond, report_outcome`
+ optional `verify` + discovered **MCP tools** (e.g. Gmail via `workspace-mcp`).

**Subsystems** (`agentd/infrastructure/`): `agents/` (file registry + bootstrap),
`autonomy/` (scheduler + cron schedule math), `channels/` (Channel framework + Memory/Email
adapters + poller), `engine/` (NativeEngine reason→act loop), `liveness/` (CallRateBrake
loop-detection + no-progress watchdog), `llm/` (LiteLLM stream + idle/request timeout),
`memory/` (**SessionStore transcripts only** — NOT a learning memory), `notify/` (5a),
`prompt.py`, `skills/` (FileSkillRegistry, static `SKILL.md`), `tasks/` (SqliteTaskStore:
tasks/goals/runs/notifications), `tools/`, `verify/`.

**Phases built:** P1 agent-as-definition · P2 autonomy (heartbeat + cron + goals + run
outcomes) · reliability guardrails (GuardedTool timeout/retry/error-norm, LLM idle/req
timeout, liveness watchdog) · 5a notify · 5b channels framework + Email adapter.

**Reliability we DO have:** per-tool timeout/retry/error-norm; LLM idle-timeout (120s) +
request-timeout; computer per-call timeout; loop-detection (CallRateBrake) + no-progress
watchdog (liveness pkg).

---

## 3. Capability comparison (verified, with OpenClaw evidence)

Legend: ✅ have · ⚠️ partial · ❌ missing.

| Capability | OpenClaw — verified | OpenClaw key files | agentd |
|---|---|---|---|
| Core tools (file/shell/web/browser/computer) | ✅ + canvas, diffs, pdf/image **vision**, **media gen** (image/video/music/tts), **device/node control** (camera, screen-record, GPS) | `src/agents/tools/`, `src/agents/openclaw-tools.ts`, `extensions/browser/`, `extensions/codex/` | ✅ core set; ❌ vision/media/device/canvas |
| **Memory / learning** | ✅ full — `MEMORY.md` + `memory/YYYY-MM-DD.md` + **SQLite + sqlite-vec embeddings + FTS5** + `memory_search`/`memory_get` + **"Dreaming"** consolidation (light/REM/deep → promotes facts, writes `DREAMS.md`) | `src/memory/root-memory-files.ts`, `packages/memory-host-sdk/src/host/{memory-schema,sqlite-vec,embeddings,query-expansion}.ts`, `extensions/memory-core/src/{dreaming,short-term-promotion,dreaming-narrative}.ts` | ❌ none (only transcripts + static MEMORY.md) |
| **Context engine / compaction** | ✅ window mgmt, auto-compaction + `/compact`, prompt-cache tracking, subagent context isolation/fork | `src/context-engine/{types,delegate}.ts`, `packages/agent-core/src/harness/compaction/compaction.ts` | ❌ no auto-compaction (long sessions will hit the wall) |
| **Sub-agents** | ✅ full — `sessions_spawn` tool, parallel (limits 4/8/5 + depth), modes run/session × isolated/**fork** × sandbox inherit/require, `sessions_yield` fan-out, registry | `src/agents/tools/sessions-spawn-tool.ts`, `src/agents/subagent-spawn.ts`, `src/agents/subagent-registry.ts`, `src/agents/tools/sessions-yield-tool.ts`, `src/config/agent-limits.ts` | ❌ none |
| **Sandbox** | ✅ Docker + SSH, per-session/agent/shared containers, fs mounts ro/rw/none, tool-policy | `src/agents/sandbox/{docker,ssh-backend,fs-bridge,workspace-mounts,tool-policy,types}.ts` | ❌ runs on host |
| **Model catalog + failover** | ✅ **137 providers**, 8 first-class APIs w/ cost/context/capability metadata + routing; primary+fallbacks chains; classified-error cross-provider failover; **tool-call-repair** | `packages/model-catalog-core/src/model-catalog-types.ts`, `src/agents/{model-fallback,failover-error}.ts`, `src/agents/embedded-agent-runner/run/{fallbacks,failover-policy,idle-timeout-breaker}.ts`, `packages/tool-call-repair/src/*` | ⚠️ single model (LiteLLM), per-agent override, **no fallback** |
| **skill_workshop** | ✅ agent **creates/edits skills at runtime** (TS/JS) | `src/agents/tools/` (skill_workshop) | ❌ static skills only |
| Autonomy depth | ✅ cron `sessionTarget` (main/isolated/current/session:key), `wakeMode`, payload kinds (agentTurn/systemEvent/command), `delivery` (none/announce/webhook), `failureAlert`, `staggerMs`; full SQLite **run-log** (token usage, provider, delivery trace, diagnostics) | `src/cron/{types,schedule,run-log,run-log-types,isolated-agent}.ts` | ⚠️ cron basics (at/in/every/daily/cron+tz) + goals + run-outcomes + notify; rest deferred |
| **Commitments** (goals++) | ✅ inferred follow-up obligations (kinds: event_check_in/deadline_check/care_check_in/open_loop), model-extracted post-turn, confidence + dueWindow + dedupe, max/day | `src/commitments/{types,extraction,runtime,config}.ts` | ⚠️ basic goals (objective + advisory budget) |
| **Auth-profiles** | ✅ multi-account OAuth/api_key/token/aws, rotation, per-reason failure counters, billing/auth backoff, per-agent order | `src/agents/auth-profiles/{types,oauth-manager,usage}.ts` | ⚠️ single Google MCP OAuth (no rotation) |
| Auto-reply | ✅ envelopes (tz/elapsed), command-detection, debouncing, group-mention routing | `src/auto-reply/{envelope,dispatch,command-detection}.ts` | ❌ (channels framework only) |
| Channels (transports) | ✅ 20+: discord/slack/telegram/whatsapp/signal/matrix/msteams/sms + transport/inbound-event/turn/plugins | `src/channels/*`, `extensions/{discord,slack,telegram,whatsapp,signal,matrix,msteams,sms}/` | ⚠️ framework + Email (Gmail MCP) + Memory; specific adapters not built |
| Reliability | ✅ idle-timeout-breaker (cost-explosion guard), tool-call-repair, net-policy redaction | `src/agents/embedded-agent-runner/run/idle-timeout-breaker.ts`, `packages/{tool-call-repair,net-policy}/` | ✅ GuardedTool + idle/req timeout + loop/no-progress watchdog (we're solid) |
| Security / governance | ✅ approvals, node command approve/reject, net-policy, sandbox tool-policy | `src/agents/sandbox/tool-policy.ts`, `packages/net-policy/` | ❌ deferred (Phase 4 — out of current scope) |
| Agent definition | directory of bootstrap MD (AGENTS/SOUL/TOOLS/IDENTITY/USER/HEARTBEAT/BOOTSTRAP) + config (id/workspace/model{primary,fallbacks}/identity/thinking/toolsAllow) | `src/agents/workspace.ts`, `src/config/types.agents.ts` | ✅ directory agent (IDENTITY/AGENTS/USER/MEMORY/HEARTBEAT + skills) + per-agent tools/skills/model/scope |
| Coding backend | ✅ Codex/ACP harness (semantic code gen) + diffs | `extensions/codex/`, `packages/acp-core/`, `src/acp/` | ⚠️ generic file-edit + shell (Claude-Code-style), no Codex/ACP |
| MCP | ✅ client (acpx) **and** server mode | `extensions/acpx/` | ⚠️ client only (no MCP-server mode) |

---

## 4. Where we're genuinely on par
1:1 **chat** (streaming, thinking display, resumable sessions, agent switching); **core tools**
(file/shell/web/browser/computer + MCP client); **agent-as-directory** definition with
per-agent tools/skills/model/scope; **basic autonomy** (cron with full basic parity +
heartbeat + goals + run outcomes + notify); **reliability fundamentals** (our GuardedTool +
watchdog ≈ their idle-breaker). A user doing everyday chat + tool use + a scheduled job
would not feel a difference. Our **clean architecture** (enforced layering, ports/adapters)
is a genuine strength for extending deliberately.

---

## 5. Gap analysis — ranked by impact (build order)

> Excludes the two the user set aside (approvals/security governance, and the specific
> channel adapters like Slack/Telegram). Effort tags are rough: **S** ≤ a few days,
> **M** ~1–2 weeks, **L** multi-week.

### #1 — Memory / learning  · impact: highest · effort: **M**
**Why:** Theirs *accumulates and self-consolidates*; ours resets each session beyond the
transcript + a static `MEMORY.md`. This is the single biggest "feels smart" gap.
**What OpenClaw does:** files (`MEMORY.md`, `memory/YYYY-MM-DD.md`) indexed into SQLite with
**sqlite-vec** (semantic) + **FTS5** (keyword), dual-score ranking with temporal decay;
`memory_search`/`memory_get` tools; **Dreaming** = scheduled consolidation in three phases
(light: scan transcripts → snippets; REM: derive themes; deep: promote top facts to
`MEMORY.md` + write a `DREAMS.md` diary). Study: `packages/memory-host-sdk/src/host/*` and
`extensions/memory-core/src/*`.
**How we'd build it (fits our layers):**
- domain: `MemoryItem` (id, agent_id, source=memory|session, text, ts, score).
- application: `MemoryStore` port (`upsert`, `search(query, k)`, `get(path, range)`).
- infrastructure: `SqliteMemoryStore` (reuse the sqlite file pattern; embeddings via an
  injected embed fn — start keyword/FTS-only, add vectors later), a `MemoryIndexer` that
  ingests session transcripts + `MEMORY.md`/daily notes.
- tools: `memory_search`, `memory_get` (always-on, like read); writing = the agent appends
  to `memory/YYYY-MM-DD.md` via the existing write tool, or a `remember` tool.
- consolidation: a cron-driven "consolidate" run (light/deep) that promotes recurring notes
  into `MEMORY.md`. Reuse our cron + run-outcome plumbing.
- **Start S, grow to M:** FTS-only memory_search + a `remember` tool + daily-notes ingest is
  the MVP; embeddings + Dreaming are the follow-on.

### #2 — Context engine / compaction · impact: high · effort: **M**
**Why:** Without it, long sessions/channels will hit the model's context limit and fail.
**What OpenClaw does:** lifecycle hooks (bootstrap/assemble/ingest/maintain/compact/afterTurn),
auto-compaction on overflow (LLM summary + `firstKeptEntryId`), manual `/compact`, prompt-cache
bookkeeping. Study: `src/context-engine/*`, `packages/agent-core/src/harness/compaction/*`.
**How we'd build it:** add a compaction step in the engine loop — when the transcript exceeds
a token budget, summarize the oldest turns into one synthetic message and keep recent N. A
`ContextPolicy` port (assemble/compact) so it's swappable. Token counting via the model/usage.

### #3 — Sub-agents · impact: high · effort: **L**
**Why:** No delegation/parallelism — a big task can't fan out (research, multi-step support).
**What OpenClaw does:** `sessions_spawn` tool spawns child runs (modes run/session ×
isolated/fork context × sandbox inherit/require), `sessions_yield` to suspend-and-resume on
completion, a registry tracking children, concurrency limits (4 top / 8 sub / 5 children /
depth 1). Study: `src/agents/subagent-spawn.ts`, `sessions-spawn-tool.ts`,
`subagent-registry.ts`, `config/agent-limits.ts`.
**How we'd build it:** a `spawn_subagent` tool that posts an internal turn for a child session
(`agent:<id>:sub:<runId>`), the gateway tracks the child run + delivers its result back; a
registry + concurrency cap; `isolated` (fresh context) first, `fork` later. Reuses our
`_run` + RunHandle machinery (we already do internal runs for cron/channel).

### #4 — Model failover + tool-call-repair · impact: med-high · effort: **M**
**Why:** Production resilience — a single provider hiccup ends our turn; theirs fails over.
**What OpenClaw does:** `model.{primary,fallbacks[]}` chains; classified errors
(timeout/rate-limit/quota/overloaded/unavailable) decide failover; idle-timeout-breaker caps
runaway cost; **tool-call-repair** recovers tool calls the model leaked as text. Study:
`src/agents/{model-fallback,failover-error}.ts`, `embedded-agent-runner/run/{fallbacks,
failover-policy,idle-timeout-breaker}.ts`, `packages/tool-call-repair/src/*`.
**How we'd build it:** a `failover_stream(models=[…])` wrapper around `litellm_stream` — on a
`done` error / idle-timeout with no output, retry the turn on the next model. AgentSpec already
carries `model`; extend to a list. tool-call-repair = a stream post-processor that detects
`[tool:name {…}]`/XML-ish leaks and promotes them to native tool calls (study their grammar).
(Note: this is exactly the documented Phase-5 roadmap item in our reliability plan.)

### #5 — Breadth: skill_workshop · media/vision · device/node · canvas · effort: **S–L each**
**Why:** Capability breadth, mostly additive (each is an isolated tool/adapter).
- **skill_workshop** (S–M): a tool that writes/edits `SKILL.md` (or code skills) at runtime +
  hot-reload (our FileSkillRegistry already reads skills fresh per turn). Study `src/agents/tools/` skill_workshop.
- **vision** (S): `image_vision`/`pdf` tools — pass an image/PDF to a vision model. We have a
  computer-use vision model already; reuse it.
- **media generation** (M): image/video/music/tts via provider adapters — pure add-on tools.
- **device/node control** (L): mobile/desktop nodes (camera, screen-record, GPS) — heavy,
  needs the node-pairing protocol; low priority for our use cases.

### #6 — Autonomy depth + commitments + auth-profiles · impact: med · effort: **M**
**Why:** Richer scheduling + "remember to follow up" + multi-account.
**What OpenClaw does:** cron `sessionTarget`/`wakeMode`/payload kinds/`delivery`/`failureAlert`,
full run-log with token usage; **commitments** (model-extracted follow-ups w/ confidence +
dueWindow); **auth-profiles** (multi-OAuth rotation + cooldown). Study `src/cron/*`,
`src/commitments/*`, `src/agents/auth-profiles/*`.
**How we'd build it:** extend our `ScheduledTask` (delivery already exists) with sessionTarget/
payload-kind; add a `commitments` table + a post-turn extractor (cron-driven); auth-profiles =
a credentials store + rotation policy in front of the MCP/channel OAuth.

### #7 — Sandbox · impact: med (security/safety) · effort: **L**
Docker/SSH isolation for tool execution. Pairs with the deferred approvals/governance work.
Study `src/agents/sandbox/*`. Lower priority until we run untrusted/remote workloads.

---

## 6. Recommended sequence
1. **Memory (MVP: FTS `memory_search` + `remember` + daily-notes ingest)** — biggest felt win, S→M.
2. **Context compaction** — prevents long-session failure; unblocks everything long-running, M.
3. **Memory consolidation ("Dreaming")** + embeddings — upgrades #1 to full, M.
4. **Model failover + tool-call-repair** — resilience, M (already on the reliability roadmap).
5. **Sub-agents** — delegation/parallelism, L.
6. Breadth (skill_workshop, vision) as needed; commitments/auth-profiles; sandbox last.

Rationale: 1–2 make the agent *feel smart and not fall over*; 4 makes it *reliable*; 5 makes
it *scale to big tasks*. Everything else is breadth we add on demand.

---

## 7. Appendix — OpenClaw map (where to read when building)
- **Memory:** `src/memory/`, `packages/memory-host-sdk/src/host/`, `extensions/memory-core/src/`
- **Context:** `src/context-engine/`, `packages/agent-core/src/harness/compaction/`
- **Sub-agents:** `src/agents/{subagent-spawn,subagent-registry,acp-spawn}.ts`, `src/agents/tools/sessions-*.ts`, `src/config/agent-limits.ts`
- **Sandbox:** `src/agents/sandbox/`
- **Model layer:** `packages/{model-catalog-core,llm-core,llm-runtime,tool-call-repair,net-policy}/`, `src/agents/{model-fallback,failover-error}.ts`, `extensions/<provider>/` (137)
- **Autonomy:** `src/cron/`, `src/commitments/`, `src/auto-reply/`, `src/agents/auth-profiles/`
- **Channels:** `src/channels/`, `extensions/{discord,slack,telegram,whatsapp,signal,matrix,msteams,sms}/`
- **Agent def + loop:** `src/agents/{workspace,harness,runtime-plan,embedded-agent-runner}/`, `src/config/types.agents.ts`
- **Tools:** `src/agents/tools/`, `src/agents/openclaw-tools.ts`, tool extensions (`codex`, `browser`, `file-transfer`, `document-extract`, `acpx`)

## 8. Honest verdict (one line)
agentd is a **clean, well-architected foundation that nails the core** (chat + tools + basic
autonomy + a real channels framework) at ~85% of that slice — but OpenClaw's **full surface**
(memory, sub-agents, context-compaction, model-failover, sandbox, skill_workshop, media/device
breadth, 137 providers, 20+ channels) puts us at ~40–50% overall. The three highest-leverage
builds to close the felt distance are **Memory → Context-compaction → Sub-agents**.

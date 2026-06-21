# Parity execution plan — closing the OpenClaw gap (minus tools-breadth + channel adapters)

> Ordered, layered, decoupled steps. **One step per turn.** Each step: respects the
> import-linter layering (main > presentation > infrastructure > application > domain),
> is independently testable, and ends with the full suite + lint green. Design refs for
> each item live in `openclaw-parity-and-roadmap.md` (OpenClaw file pointers).
>
> Status: ☐ todo · ◐ in progress · ☑ done

## Tier 0 — Harness / persona (base prompt; the cheap, biggest-felt win)
- ☑ **S1. Persona core.** *Done (268 tests, lint kept).* `PERSONA` constant in
  `infrastructure/prompt.py` injected into the base prompt after the identity line and
  before the agent's IDENTITY (so IDENTITY can refine); `config.persona_enabled` (default
  on, `AGENTD_PERSONA=0` off). Disposition: useful-not-performative · resourceful-then-ask-
  ONE-Q · propose+confirm on big/irreversible work · honesty/NEVER-fabricate · judgment ·
  trust(bold-on-reversible/careful-on-external). Applies to EVERY agent + plain chat.
- ☑ **S2. Self-knowledge / capabilities section.** *Done (272 tests, lint kept).*
  `_capabilities_section(tools, config)` in `infrastructure/prompt.py` (injected after Tooling)
  builds a "## What you are" block DYNAMICALLY from available organs: schedule(cron)/wake(heartbeat)/
  reach-user(notify)/be-reached(channels)/remember(memory tools)/delegate(sub-agent tools). Empty
  for a bare setup (no noise); auto-grows as S5/S8 land. Closes with the **propose-architecture**
  nudge ("ongoing work → propose a cron job that does it, records outcome, notifies on blocker —
  confirm first"). Honest: no autonomy ⇒ no schedule/wake claim.
- ☑ **S0.5. Persona as editable file.** *Done.* `SOUL.md` at repo root is the editable
  persona (`config.persona_file`, default repo SOUL.md, `AGENTD_PERSONA_FILE` override);
  `_load_persona()` reads it, falls back to the `PERSONA` constant if missing. Removed
  `SOUL.md` from `CONTEXT_FILES` (no double-load). *Layers: config + infrastructure/prompt.*
- ☑ **S3. Honesty-by-default + verify gate (prompt-level).** *Done (280 tests).* Flipped
  `completeness_check` default → **on** (the "## Before You Finish" anti-fabrication section is
  now default; getattr fallback also True). Strengthened "## Verify Before You Send (required)"
  to a hard mandate: MUST verify substantial/delivered answers; a NEEDS-WORK result MUST be
  fixed + re-verified; never send a failed-verify answer. *(True engine-level hard-blocking of
  the send deferred — kept the tested loop intact; persona + default-on prompt enforce it strongly.)*

## Tier 1 — Memory & context
- ☑ **S4. Memory data layer.** *Done (280 tests, lint kept).* `domain/memory.py` MemoryItem;
  `application/interfaces/memory_bank.py` MemoryBank port (save/search/get/recent); infra
  `memory/bank.py` SqliteMemoryBank (`<state_dir>/memory.sqlite`, FTS5 with LIKE fallback,
  agent-scoped). *Layers: domain + application + infrastructure.*
- ☑ **S5. Memory tools + wiring.** *Done.* `infrastructure/tools/memory_tools.py`
  remember / memory_search / memory_get (context-aware via run-context); `config.memory_enabled`
  (default OFF, `AGENTD_MEMORY=1`); `container.build_memory_bank` threaded into build_service →
  build_tools (registered only when enabled). The S2 capabilities bullet ("Remember across
  sessions") auto-appears once the tools are present. *Layers: infrastructure + main.*
- ☑ **S6. Memory consolidation.** *Done (287 tests).* `infrastructure/memory/consolidate.py`
  collapses exact-duplicate notes per agent (keeps newest); `MemoryConsolidateTool` (+ bank
  `delete`) so a cron/heartbeat job can tidy memory. *(LLM-summary promotion to MEMORY.md = later
  refinement.)* *Layers: infrastructure.*
- ☑ **S7. Context compaction.** *Done.* application `ContextPolicy` port (`prepare(messages)`);
  infra `WindowContextPolicy` (boundary-safe truncation — keeps recent N from a user turn, never
  splits a tool-call/result); engine hook (`context_policy`, non-mutating, **default None = send
  all**); `config.context_max_messages` (0=off, `AGENTD_CONTEXT_MAX`). *(LLM-summary policy slots
  in behind the same port.)* *Layers: application + infrastructure + engine.*

## Tier 2 — Sub-agents & skills
- ☑ **S8. Sub-agent spawn.** *Done.* `infrastructure/tools/subagent_tool.py` SpawnSubagentTool
  (injected spawn callable — infra never imports presentation); gateway `_spawn_subagent` runs a
  CHILD turn on `agent:<id>:sub:<runId>` in its OWN asyncio.Task (context-isolated so it can't
  clobber the parent's run-context), reuses `_run` + `_last_answer`, returns the child's answer;
  concurrency cap (`subagent_max`) + depth-1 guard; `_build_subagents` registers it (guarded) when
  `config.subagents_enabled` (default OFF, `AGENTD_SUBAGENTS=1`). *Layers: infra (tool) + presentation.*
- ☑ **S9. Delegation disposition + parallel.** *Done.* Strengthened the S2 "Delegate" capability
  bullet; spawn tool is `concurrency="parallel"` (agent can fan several out) + `default_timeout_sec=None`.
  *(Full yield/async-completion orchestration = later refinement.)* *Layers: infrastructure/prompt.*
- ☑ **S10. skill_workshop tool.** *Done.* `infrastructure/tools/skill_tool.py` SkillWorkshopTool
  (create/update/list → writes `<skills_dir>/<slug>/SKILL.md`, frontmatter+body; FileSkillRegistry
  reads fresh → available next turn). `config.skill_workshop` (default OFF, `AGENTD_SKILL_WORKSHOP=1`).
  *Layers: infrastructure (tool).*

## Tier 3 — Robustness & governance
- ☑ **S11. Model failover.** *Done (291 tests, lint kept).* `infrastructure/llm/failover.py`
  `make_failover_stream(inner, fallbacks)` wraps the stream: on a CLEAN error (model ends `error`
  with no output yet) retries the next candidate; passes the error through once output streamed
  (no dup); composes with idle-timeout. `config.model_fallbacks` (default [] => returns inner
  unwrapped; `AGENTD_MODEL_FALLBACKS`). Container wraps stream_fn when fallbacks set. *(Per-agent
  fallback lists = later refinement; global for now.)* *Layers: infrastructure + main.*
- ⊘ **S12. Tool-call-repair.** *Deferred (low ROI).* Native-tool-calling models (Gemini/Claude)
  rarely leak tool calls as text; the full grammar/stream-injection is complex for little gain.
  Park with S13/S17.
- ⊘ **S13. Approval / policy chokepoint.** *Deferred (user: governance, not power).* Build later
  at the GuardedTool chokepoint (also fixes the computer-tool ToS auto-ack).
- ☑ **S14. Autonomy depth.** *Done (298 tests, lint kept).* `failure_alert` — `ScheduledTask`
  field + tasks-table migration + cron-tool param + store `consecutive_failures(task_id)`; gateway
  `_run` AUTO-PAUSES a job + notifies after N consecutive failures (so a broken job stops running
  forever). *(sessionTarget/wakeMode/payload-kinds = marginal for our model, deferred.)* *Layers:
  domain + infrastructure + presentation.*
- ☑ **S15. Commitments.** *Done.* domain `Commitment`; SqliteTaskStore commitments table +
  add/list/resolve; `commitment` tool (add/list/done/drop, agent-scoped) — an open-loop/follow-up
  tracker (built with cron/goal when autonomy on). *(LLM post-turn auto-extraction = refinement;
  ours is explicit-record.)* *Layers: domain + infrastructure.*
- ☑ **S16. Auth-profiles.** *Done.* domain `AuthProfile`; application `AuthProfileStore` port;
  `infrastructure/auth/` SqliteAuthProfileStore — LRU rotation + cooldown-on-failure across a
  provider's accounts. *(Selection state + rotation logic; wiring into the MCP/model-provider auth
  is the integration step.)* *Layers: domain + application + infrastructure.*

## Tier 4 — Heavy / platform
- ◐ **S17. Sandbox.** *Seam done.* application `Sandbox` port; `infrastructure/sandbox/`
  LocalSandbox (host exec) + `build_sandbox` factory; `config.sandbox` (default local). Docker/SSH
  isolating adapters + routing exec/computer through it = the heavy integration (deferred, pairs
  with S13). *Layers: application (port) + infrastructure.*
- ◐ **S18. Platform.** *Seam done.* Agent-definition **versioning** — `AgentSpec.version` (from
  agent.toml) surfaced in `agents.list`. Plugin-registration + MCP-server-mode + versioned-def
  resolution = large separate efforts (deferred). *Layers: domain + infrastructure + presentation.*

## Principles (apply to every step)
- **Decoupled:** new behavior behind a port; concrete impls in infrastructure; default-OFF or
  back-compat where it could change existing behavior.
- **SOLID:** one concern per module; depend on interfaces, not impls; additive over invasive.
- **Green gate:** every step ends with `pytest tests/` + `lint-imports` passing, and the
  reactive/default path unchanged unless the step explicitly opts in.

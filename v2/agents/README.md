# agents/ — independent agent definitions

**An agent is a directory.** Drop a folder in here and it becomes an independent
agent; delete the folder and it's gone. The single-agent app is the `main` agent,
**synthesized from config** — you don't need any files here for the default app to
work (this folder can be empty).

```
agents/<id>/
  agent.toml      # optional config (below). Without it, sensible defaults apply.
  IDENTITY.md     # persona — who it is, tone, boundaries
  AGENTS.md       # operating rules / red lines
  USER.md         # user / business context (optional)
  MEMORY.md       # long-term learned facts (optional; agent-writable later)
  HEARTBEAT.md    # autonomous-tick checklist — loaded ONLY on a heartbeat run (Phase 2)
  skills/         # (Phase 3) drop-in SKILL.md playbooks for this agent
  workspace/      # (optional) the agent's working dir; defaults to the global workspace
```

`<id>` is lowercase letters/digits/`-`/`_`. The id `main` is special: it keeps the
**legacy session path** (`<state_dir>/sessions/`) so existing transcripts are
preserved; every other agent partitions to `<state_dir>/agents/<id>/sessions/`.

## agent.toml

```toml
name = "Acme Support"          # persona name (defaults to the dir id)
model = "gemini/gemini-2.5-flash"   # per-agent model override (live)
skills = ["github", "refund-policy"]  # skill allowlist (omit = all skills)
heartbeat = "15m"              # autonomous wake interval — needs AGENTD_AUTONOMY=1 (Phase 2a). Pair with HEARTBEAT.md.

[tools]
allow = ["read", "web_search", "google__*"]   # omit = all tools; "name*" = prefix (whole MCP server)
deny  = ["computer"]                            # deny always wins
```

All fields are optional. `IDENTITY.md` / `AGENTS.md` / `USER.md` / `MEMORY.md` are
concatenated into the agent's system prompt (each under its own heading, size-capped).

## Routing

A message reaches an agent by **session key** `agent:<id>:<channel>:<peer>`. Legacy
plain keys (e.g. `default`) and unknown ids resolve to `main`. Different agents are
different sessions, so they run concurrently in one gateway.

> Scope: Phase 1 (definitions + routing + scoping + per-agent sessions). Autonomy
> (heartbeat/cron/goals), memory/learning, sub-agents, and channels are later phases
> — see `planning/platform/agents/agent-system-plan.md`.

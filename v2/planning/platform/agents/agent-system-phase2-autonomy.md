# Phase 2 — Autonomy (design note)

Make an agent **run on its own**: wake on a schedule (heartbeat), run scheduled jobs
(cron), and pursue bounded long tasks (goals) — with **no user present**. Built
local-contained but deployment-agnostic; **default OFF**, fully additive, the
reactive chat path untouched.

> Status: **DESIGN — awaiting LGTM**, no code yet.
> Diagrams: [`agent-autonomy-detail.puml`](agent-autonomy-detail.puml) (components),
> [`agent-heartbeat-sequence.puml`](agent-heartbeat-sequence.puml) (one tick).
> Builds on Phase 1 (agent registry + per-agent session routing + tool scoping).

---

## 1. The one new concept: RUN MODE
Today every turn is *interactive* (a user sent a message). Phase 2 adds two more:

| Mode | Trigger | Extra bootstrap | Extra tool |
|---|---|---|---|
| `interactive` | user/client message | — | — |
| `heartbeat` | scheduler tick (interval) | **HEARTBEAT.md** | **heartbeat_respond** |
| `cron` | a due scheduled job | the job's payload | — |

Run mode is the **second tool-scoping axis** on top of Phase 1's per-agent scope:
```
tools = select_tools(all_tools, agent)        # Phase 1: per-agent allow/deny
      ↳ apply_mode(mode)                       # Phase 2: + heartbeat_respond iff mode==heartbeat
prompt = persona + bootstrap (+ HEARTBEAT.md iff mode==heartbeat)
```
`interactive` assembles exactly as today → **reactive path byte-for-byte unchanged.**

---

## 2. Components (per layer, SOLID seams)

**domain/** `autonomy.py`
- `ScheduledTask` (id, agent_id, kind `at|every|cron`, schedule, payload, next_due, enabled)
- `Goal` (id, agent_id, session_key, objective, token_budget, tokens_spent, status)
- `RunMode` enum (`interactive|heartbeat|cron`)

**application/**
- `interfaces/tasks.py` — **`TaskStore` port** (add/list/cancel tasks; due(); record run; goal CRUD; heartbeat next_due). The app depends on this, never on SQLite.
- `services/autonomy_service.py` — **`AutonomyService`**: given a due agent+mode, posts a turn through the gateway and records the outcome. The only thing that knows "a tick = an internal client message."

**infrastructure/**
- `tasks/sqlite_store.py` — **`SqliteTaskStore`** implements `TaskStore` (the durable ledger; schema §4).
- `autonomy/scheduler.py` — **the ONE shared loop** (§5): per-agent due-check, active-hours, flood-guard.
- `tools/cron_tool.py`, `tools/goal_tool.py`, `tools/heartbeat_tool.py` — the three tools (§6).
- `agents/bootstrap.py` — gains an optional HEARTBEAT.md load (heartbeat mode only).

**presentation/** `gateway.py`
- internal `post_turn(agent_id, mode, text)` — a thin entry the scheduler calls; **reuses `handle_message`** (no second run path). Tags the run with its mode.

**application/services/agent_service.py**
- `handle_message(..., mode="interactive")` — the only signature change: an optional mode that drives mode-aware tool + bootstrap assembly. Default = today.

---

## 3. What's shared vs per-agent (settled earlier)
- **ONE** scheduler loop + **ONE** SQLite ledger serve every agent (not one per agent).
- **Per-agent** = the rows (jobs/goals/heartbeat keyed by `agent_id`), the HEARTBEAT.md, the sessions/memory. Each fired turn is a full per-agent run (its persona, tools, model, session).

---

## 4. SQLite ledger schema
One DB at `<state_dir>/autonomy.sqlite`, every row tagged `agent_id` (delete an agent ⇒ `DELETE WHERE agent_id=…`).

```sql
CREATE TABLE tasks (              -- cron jobs (the cron TOOL writes; the runner fires)
  id TEXT PRIMARY KEY, agent_id TEXT NOT NULL,
  kind TEXT NOT NULL,            -- 'at' | 'every' | 'cron'
  schedule TEXT NOT NULL,        -- ISO time | interval | cron expr
  payload TEXT,                  -- the instruction to run
  next_due TEXT, enabled INTEGER DEFAULT 1, created_at TEXT
);
CREATE TABLE heartbeats (        -- per-agent tick schedule (from agent.toml interval)
  agent_id TEXT PRIMARY KEY, interval TEXT, next_due TEXT, last_run TEXT
);
CREATE TABLE goals (             -- objective + token budget, per agent/session
  id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, session_key TEXT,
  objective TEXT NOT NULL, token_budget INTEGER, tokens_spent INTEGER DEFAULT 0,
  status TEXT DEFAULT 'active',  -- active | complete | blocked
  created_at TEXT, updated_at TEXT
);
CREATE TABLE runs (              -- audit: what fired + outcome
  id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, source TEXT,  -- heartbeat|cron|interactive
  task_id TEXT, started_at TEXT, finished_at TEXT, outcome TEXT
);
```
**Restart recovery:** on boot the scheduler reads `next_due` across `tasks`+`heartbeats` and resumes — a job set before a reboot still fires.

---

## 5. The scheduler loop (one shared)
```
while autonomy_enabled:
    now = clock()
    for agent in agents_with_heartbeat:
        if now >= heartbeats.next_due[agent] and in_active_hours(now)
           and lane_free(agent):                       # one active run per session
            AutonomyService.fire(agent, mode=heartbeat)
            heartbeats.next_due = now + interval
    for task in tasks.due(now):                         # next_due <= now, enabled
        AutonomyService.fire(task.agent_id, mode=cron, payload=task.payload)
        advance_or_disable(task)                        # 'at' disables; 'every'/'cron' reschedules
    sleep(until = min(all next_due, short_tick))
```
- **Flood guard:** skip if the agent's lane is busy (reuse the gateway's "one run per session").
- **Active hours / interval:** from config + per-agent `agent.toml`.
- **Staggering:** phase-offset so N agents don't all fire on the same second.

---

## 6. The three tools
- **`cron`** (`add|list|remove`) — writes/reads a `tasks` row. Agent: *"every weekday 9am, summarize new emails."*
- **`create_goal` / `update_goal` / `get_goal`** — `create_goal(objective, token_budget)` opens a goal; `get_goal()` returns remaining budget; `update_goal(status)` marks `complete|blocked`. The **engine reports token usage per turn** → `goals.tokens_spent`; the agent self-stops when budget is exhausted.
- **`heartbeat_respond`** (heartbeat mode only) — `outcome, notify, summary, notificationText, nextCheck`. How a tick reports back + decides whether to ping you.

---

## 7. Heartbeat tick — end to end (see the sequence diagram)
1. Scheduler: agent X is due → `AutonomyService.fire(X, heartbeat)`.
2. `post_turn("agent:X:heartbeat", mode=heartbeat, "Read HEARTBEAT.md. Act, or reply nothing-to-do.")`.
3. `handle_message(mode=heartbeat)` assembles X's persona **+ HEARTBEAT.md** and X's tools **+ heartbeat_respond**.
4. Engine runs: agent checks things (tools), does work or not.
5. Agent calls `heartbeat_respond(outcome, notify, nextCheck)`.
6. AutonomyService records the run, sets `next_due`; if `notify`, sends the summary to the user (origin/last contact); else stays silent.

Heartbeat ticks use a **dedicated per-agent session** (`agent:X:heartbeat`) so they have their own continuity + transcript, isolated from interactive chats.

---

## 8. Config (all default to OFF / safe)
```
autonomy_enabled: bool = False          # master switch (AGENTD_AUTONOMY)
heartbeat_default_interval: str = ""     # e.g. "30m"; per-agent agent.toml overrides
heartbeat_active_hours: str = ""         # e.g. "08:00-22:00" (empty = always)
```
An agent participates only if **autonomy_enabled AND it declares a `heartbeat` interval** (or has cron jobs). Nothing fires by default.

---

## 9. Slicing (ship incrementally, suite green each step)
- **2a — Heartbeat spine:** `RunMode`, mode-aware assembly, `HEARTBEAT.md` load, `heartbeat_respond`, a minimal scheduler (interval only) + `AutonomyService` + a tiny `heartbeats` table. **Milestone: the agent wakes itself and reports back.**
- **2b — Cron + goals + full ledger:** `cron` tool + runner, `goals` tools + token accounting, the full SQLite schema + restart recovery.

---

## 10. Decisions to confirm at LGTM
1. **Run-mode model** `interactive|heartbeat|cron` driving tool/bootstrap assembly — OK?
2. **One shared SQLite ledger** with `agent_id` (vs per-agent files) — OK?
3. **Dedicated per-agent heartbeat session** (`agent:X:heartbeat`) for tick continuity — OK?
4. **Autonomy default OFF**, opt-in per agent via a `heartbeat` interval / cron job — OK?
5. **Build 2a first** (heartbeat spine), then 2b — OK?

---

## 11. Tests (per slice)
- Scheduler fires a due agent (injected clock); respects active-hours; skips a busy lane.
- `TaskStore`: add/list/cancel; **survives "restart"** (reopen DB) and re-fires; `next_due` advance.
- Mode assembly: heartbeat run includes `heartbeat_respond` + HEARTBEAT.md; interactive excludes both.
- Goal: create/get/update + budget accounting; auto-stop at budget.
- **Back-compat:** autonomy off ⇒ reactive path identical; existing suite stays green.

"""ScheduledTask — a durable cron/scheduled job for an agent (Phase 2b).

A task says: at/after some time, run `payload` as agent `agent_id` in `session_key`.
Schedule kinds:
  - "at"    one-shot absolute time (disabled after it fires)
  - "every" recurring fixed interval (`every_seconds`)
  - "cron"  recurring cron EXPRESSION (`cron_expr`) in IANA timezone `tz`
Pure domain: no IO. Computing the NEXT fire (esp. for cron) needs a library, so it
lives in infrastructure (`autonomy/schedule.py`), not here. The SQLite store persists
these so jobs survive a gateway restart; the scheduler fires whichever are due.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScheduledTask:
    id: str
    agent_id: str
    session_key: str            # where the run executes (resolves to agent_id)
    kind: str                   # "at" | "every" | "cron"
    payload: str                # the instruction the agent runs (or text to deliver)
    next_due: float             # epoch seconds
    every_seconds: float | None = None   # for kind="every"
    cron_expr: str | None = None         # for kind="cron" (e.g. "55 19 * * 6")
    tz: str | None = None                # IANA timezone for the cron expr (e.g. "Asia/Tokyo")
    enabled: bool = True
    created_at: float = 0.0
    delivery: str = "run"       # "run" = agent executes payload | "message" = deliver verbatim
    failure_alert: int = 0      # notify the user after N consecutive failed runs (0 = off)


@dataclass(frozen=True)
class RunRecord:
    """One fire of a scheduled task — the audit/history row ('what ran when').

    ``status`` is the headline (running | ok | blocked | failed | error | aborted):
    engine-level error/aborted, else the agent's declared outcome folded in.
    ``outcome`` is the raw agent declaration (done | blocked | failed | None) and
    ``detail`` its one-line reason — so history shows *did it actually work*, not
    just *did it run*.
    """

    id: str
    task_id: str
    agent_id: str
    started_at: float
    finished_at: float | None = None
    status: str = "running"      # running | ok | blocked | failed | error | aborted
    outcome: str | None = None   # agent-declared: done | blocked | failed
    detail: str = ""             # one-line reason (e.g. "needs Google Drive auth")


def resolve_run_outcome(
    base_status: str,
    declared: tuple[str, str] | None,
    *,
    enforce: bool,
    is_cron: bool,
) -> tuple[str, str | None, str]:
    """RUN-seam policy (pure): fold the agent's declared outcome into the headline status.

    Precedence: an engine-level error/abort in ``base_status`` always wins. Otherwise a
    declared outcome maps done->ok / blocked->blocked / failed->failed. The reliability
    rule: when ``enforce`` is on and a CRON run finished ``ok`` but the agent declared
    NOTHING (``report_outcome`` skipped), the run is marked ``incomplete`` — so it can't
    be logged as a silent success it never verified. Returns (status, outcome, detail).
    """
    outcome: str | None = None
    detail = ""
    status = base_status
    if declared:
        outcome, detail = declared
        if status == "ok":
            status = {"done": "ok", "blocked": "blocked", "failed": "failed"}.get(outcome, "ok")
    elif enforce and is_cron and status == "ok":
        status, detail = "incomplete", "run finished without the agent declaring an outcome"
    return status, outcome, detail


@dataclass(frozen=True)
class Goal:
    """An objective (+ optional token budget) bounding a long task. The agent checks
    it (`get`) to stay on-target and marks it complete/blocked. Budget is advisory in
    this phase — the agent self-limits; hard loop-enforcement is a later refinement."""

    id: str
    agent_id: str
    session_key: str
    objective: str
    token_budget: int | None = None
    status: str = "active"      # active | complete | blocked
    created_at: float = 0.0

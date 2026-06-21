"""cron — the agent schedules its own jobs to run later (Phase 2b).

Writes durable tasks to the TaskStore; the shared scheduler fires the due ones
(surviving a gateway restart). The task belongs to the calling agent (from the
run-context) and runs in `agent:<id>:cron` so it routes back to that agent.

Schedules: `cron` (full cron expression + IANA `tz` — day-of-week etc.), `daily`
(HH:MM local), `every` (interval), `in` (one-shot delay), `at` (one-shot ISO time).
Actions: add, list, get, update, remove, run (fire now).
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime

from agentd.application.run_context import current_run_context
from agentd.domain.autonomy import ScheduledTask
from agentd.infrastructure.autonomy.schedule import resolve_schedule

from . import Tool, ToolResult


class CronTool(Tool):
    name = "cron"
    label = "Cron"
    description = (
        "Schedule your own work to run later — durable, survives gateway restarts. You "
        "run INSIDE a persistent gateway with a built-in scheduler, so use THIS for "
        "anything time-based (reminders, check-back-later, recurring jobs). Do NOT emulate "
        "scheduling with exec sleep/loops, and NEVER suggest an external scheduler (OS "
        "cron / Windows Task Scheduler) — they are unnecessary.\n"
        "action='add' with a schedule + payload. Schedule (pick one): cron='55 19 * * 6' "
        "(a cron expression — best for day-of-week / exact times; pair with tz='Asia/Tokyo'); "
        "daily='19:30' (every day at a local time); every='30m' (interval); in='2h' (once "
        "after a delay); at='2026-06-20T09:00' (once at an ISO time). payload = the "
        "instruction to run. deliver='run' (default) executes payload; deliver='message' "
        "sends it verbatim.\n"
        "Other actions: list (your jobs), get (id), update (id + new schedule/payload), "
        "remove (id), run (id — fire now), runs (history; optional id), status (overall + "
        "per-agent summary of what's scheduled), wake (text — run an ad-hoc nudge now; "
        "optional agentId)."
    )
    parameters = {
        "type": "object",
        "required": ["action"],
        "properties": {
            "action": {"type": "string",
                       "enum": ["add", "list", "get", "update", "remove", "run", "runs", "status", "wake"]},
            "payload": {"type": "string",
                        "description": "Instruction to run, or text to deliver (add/update)."},
            "deliver": {"type": "string", "enum": ["run", "message"],
                        "description": "'run' (default) executes payload; 'message' sends it verbatim."},
            "failure_alert": {"type": "integer",
                              "description": "Auto-pause the job + alert the user after this many "
                                             "consecutive failures (0 = off)."},
            "cron": {"type": "string",
                     "description": "Cron expression, e.g. '55 19 * * 6' (Sat 19:55). Day-of-week capable."},
            "tz": {"type": "string", "description": "IANA timezone for the cron expression, e.g. 'Asia/Tokyo'."},
            "daily": {"type": "string", "description": "Run every day at this local HH:MM, e.g. '19:30'."},
            "every": {"type": "string", "description": "Recurring interval, e.g. '30m', '1h', '1d'."},
            "in": {"type": "string", "description": "Run once after this delay, e.g. '2h'."},
            "at": {"type": "string", "description": "Run once at this ISO time, e.g. '2026-06-20T09:00'."},
            "id": {"type": "string", "description": "Task id (get/update/remove/run; optional for runs)."},
            "text": {"type": "string", "description": "Ad-hoc instruction to run now (action=wake)."},
            "agentId": {"type": "string", "description": "Wake a different agent (action=wake); defaults to you."},
        },
    }
    default_retryable = False
    concurrency = "sequential"

    def __init__(self, task_store):
        self._store = task_store

    async def execute(self, tool_call_id, params, abort, on_update=None):
        action = (params.get("action") or "").strip()
        ctx = current_run_context()
        agent_id = ctx.agent_id if ctx else "main"

        if action == "status":
            return self._status(agent_id)
        if action == "runs":
            return self._runs(agent_id, params)
        if action == "wake":
            return self._wake(agent_id, params)

        if action == "list":
            tasks = self._store.list(agent_id)
            if not tasks:
                return ToolResult.text("No scheduled jobs.")
            return ToolResult.text(
                "Scheduled jobs:\n" + "\n".join(self._fmt(t) for t in tasks),
                details=[t.__dict__ for t in tasks])

        if action in ("get", "update", "remove", "run"):
            tid = (params.get("id") or "").strip()
            task = self._store.get(tid) if tid else None
            if action == "remove":
                ok = self._store.remove(tid) if tid else False
                return ToolResult.text(f"removed {tid}" if ok else f"no such job: {tid}",
                                       is_error=not ok)
            if task is None:
                return ToolResult.text(f"no such job: {tid}", is_error=True)
            if action == "get":
                return ToolResult.text(self._fmt(task), details=task.__dict__)
            if action == "run":
                self._store.update(tid, next_due=time.time(), enabled=1)  # fires next poll
                return ToolResult.text(f"will run shortly: [{tid}] {task.payload[:60]}")
            if action == "update":
                return self._update(tid, params)

        if action == "add":
            payload = (params.get("payload") or "").strip()
            if not payload:
                return ToolResult.text("payload is required for action=add", is_error=True)
            try:
                sched = resolve_schedule(params)
            except ValueError as e:
                return ToolResult.text(str(e), is_error=True)
            deliver = (params.get("deliver") or "run").strip()
            task = ScheduledTask(
                id=uuid.uuid4().hex[:12], agent_id=agent_id,
                session_key=f"agent:{agent_id}:cron", payload=payload,
                enabled=True, created_at=time.time(),
                delivery=deliver if deliver in ("run", "message") else "run",
                failure_alert=int(params.get("failure_alert") or 0),
                **sched,
            )
            self._store.add(task)
            return ToolResult.text(f"scheduled {self._fmt(task)}", details=task.__dict__)

        return ToolResult.text(f"unknown action: {action}", is_error=True)

    def _status(self, agent_id: str):
        """Overall + per-agent summary of what's scheduled — the 'what's up' dashboard."""
        active = [t for t in self._store.list(None) if t.enabled]
        by_agent: dict[str, list] = {}
        for t in active:
            by_agent.setdefault(t.agent_id, []).append(t)
        lines = [f"Autonomy: {len(active)} active job(s) across {len(by_agent)} agent(s)."]
        if active:
            lines.append(f"Next fire anywhere: {self._fmt(min(active, key=lambda t: t.next_due))}")
        for aid in sorted(by_agent):
            ts = by_agent[aid]
            nd = datetime.fromtimestamp(min(t.next_due for t in ts)).strftime("%Y-%m-%d %H:%M")
            lines.append(f"  - {aid}{' (you)' if aid == agent_id else ''}: {len(ts)} job(s), next {nd}")
        runs = self._store.recent_runs(limit=5)
        if runs:
            lines.append("Recent runs:")
            for r in runs:
                when = datetime.fromtimestamp(r.started_at).strftime("%m-%d %H:%M")
                tail = f" — {r.detail}" if r.detail else ""
                lines.append(f"  - {when} {r.agent_id} [{r.task_id}] {r.status}{tail}")
        return ToolResult.text("\n".join(lines),
                               details={"active": len(active), "agents": sorted(by_agent)})

    def _runs(self, agent_id: str, params: dict):
        """Run history — for a specific job (id), else this agent's recent runs."""
        tid = (params.get("id") or "").strip() or None
        runs = self._store.recent_runs(agent_id=None if tid else agent_id, task_id=tid, limit=20)
        if not runs:
            return ToolResult.text("No run history yet.")
        lines = [f"{datetime.fromtimestamp(r.started_at).strftime('%Y-%m-%d %H:%M')} "
                 f"[{r.task_id}] {r.agent_id} -> {r.status}"
                 + (f" — {r.detail}" if r.detail else "") for r in runs]
        return ToolResult.text("Run history:\n" + "\n".join(lines),
                               details=[r.__dict__ for r in runs])

    def _wake(self, agent_id: str, params: dict):
        """An ad-hoc 'do this now' nudge — a one-shot task due immediately."""
        text = (params.get("text") or params.get("payload") or "").strip()
        if not text:
            return ToolResult.text("wake needs 'text' (what to do now)", is_error=True)
        target = (params.get("agentId") or "").strip() or agent_id
        task = ScheduledTask(
            id=uuid.uuid4().hex[:12], agent_id=target, session_key=f"agent:{target}:cron",
            kind="at", payload=text, next_due=time.time(), every_seconds=None,
            enabled=True, created_at=time.time(), delivery="run")
        self._store.add(task)
        return ToolResult.text(f"waking {target} now: {text[:60]}", details=task.__dict__)

    def _update(self, tid: str, params: dict):
        fields: dict = {}
        if any(params.get(k) for k in ("cron", "daily", "every", "in", "at")):
            try:
                fields.update(resolve_schedule(params))
            except ValueError as e:
                return ToolResult.text(str(e), is_error=True)
            fields["enabled"] = 1
        if params.get("payload"):
            fields["payload"] = params["payload"].strip()
        if params.get("deliver") in ("run", "message"):
            fields["delivery"] = params["deliver"].strip()
        if not fields:
            return ToolResult.text("nothing to update (give a new schedule/payload/deliver)",
                                   is_error=True)
        self._store.update(tid, **fields)
        return ToolResult.text(f"updated {self._fmt(self._store.get(tid))}")

    @staticmethod
    def _fmt(t) -> str:
        when = datetime.fromtimestamp(t.next_due).strftime("%Y-%m-%d %H:%M")
        if t.kind == "cron":
            sched = f"cron '{t.cron_expr}'" + (f" {t.tz}" if t.tz else "") + f" (next {when})"
        elif t.kind == "every":
            sched = f"every {int(t.every_seconds)}s (next {when})"
        else:
            sched = f"at {when}"
        state = "" if t.enabled else " (done)"
        deliv = "" if t.delivery == "run" else f" [{t.delivery}]"
        return f"[{t.id}] {sched}{state}{deliv}: {t.payload[:60]}"

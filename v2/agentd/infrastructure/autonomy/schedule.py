"""Schedule math — compute the next fire time for a ScheduledTask (Phase 2b).

Cron-expression support lives HERE (not the domain) because it needs `croniter` +
`zoneinfo`. A cron expr is interpreted in its IANA timezone as WALL-CLOCK time (not
UTC) — so "55 19 * * 6" / "Asia/Tokyo" means Saturdays 19:55 in Tokyo regardless of
where the gateway runs. Omitted tz => the gateway host's local timezone.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo

from agentd.infrastructure.autonomy.scheduler import parse_interval


def _zone(tz: str | None) -> tzinfo:
    return ZoneInfo(tz) if tz else (datetime.now().astimezone().tzinfo or ZoneInfo("UTC"))


def cron_valid(expr: str) -> bool:
    try:
        from croniter import croniter
    except ImportError:
        return False
    try:
        return bool(croniter.is_valid(expr))
    except Exception:  # noqa: BLE001
        return False


def cron_next(expr: str, tz: str | None, after: float) -> float:
    """Epoch of the next time `expr` fires (in tz wall-clock), strictly after `after`."""
    from croniter import croniter

    base = datetime.fromtimestamp(after, _zone(tz))
    return croniter(expr, base).get_next(datetime).timestamp()


def next_daily(hhmm: str) -> float:
    """Epoch of the next occurrence of local time HH:MM (today if still ahead, else tomorrow)."""
    try:
        h, m = (int(x) for x in str(hhmm).split(":"))
    except (ValueError, TypeError):
        raise ValueError(f"invalid 'daily' time (use HH:MM, e.g. 19:30): {hhmm}")
    now = datetime.now().astimezone()
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target.timestamp()


def resolve_schedule(params: dict) -> dict:
    """Turn schedule params (one of cron/daily/every/in/at) into the task's schedule
    fields (kind/next_due/every_seconds/cron_expr/tz). Raises ValueError on bad input.
    Shared by the cron tool (agent-driven) and the gateway cron.* methods (client-driven)."""
    now = time.time()
    if params.get("cron"):
        expr = str(params["cron"]).strip()
        if not cron_valid(expr):
            raise ValueError(f"invalid cron expression: {expr}")
        tz = (params.get("tz") or "").strip() or None
        if tz:
            try:
                ZoneInfo(tz)
            except Exception:
                raise ValueError(f"invalid timezone: {tz}")
        return {
            "kind": "cron",
            "next_due": cron_next(expr, tz, now),
            "every_seconds": None,
            "cron_expr": expr,
            "tz": tz,
        }
    if params.get("daily"):
        return {
            "kind": "every",
            "next_due": next_daily(params["daily"]),
            "every_seconds": 86400.0,
            "cron_expr": None,
            "tz": None,
        }
    if params.get("every"):
        sec = parse_interval(params["every"])
        if not sec:
            raise ValueError(f"invalid 'every' interval: {params['every']}")
        return {
            "kind": "every",
            "next_due": now + sec,
            "every_seconds": float(sec),
            "cron_expr": None,
            "tz": None,
        }
    if params.get("in"):
        sec = parse_interval(params["in"])
        if not sec:
            raise ValueError(f"invalid 'in' delay: {params['in']}")
        return {
            "kind": "at",
            "next_due": now + sec,
            "every_seconds": None,
            "cron_expr": None,
            "tz": None,
        }
    if params.get("at"):
        try:
            dt = datetime.fromisoformat(params["at"])
        except ValueError:
            raise ValueError(f"invalid 'at' time (ISO like 2026-06-20T09:00): {params['at']}")
        if dt.tzinfo is None:
            dt = dt.astimezone()
        return {
            "kind": "at",
            "next_due": dt.timestamp(),
            "every_seconds": None,
            "cron_expr": None,
            "tz": None,
        }
    raise ValueError("provide a schedule: one of cron, daily, every, in, at")


def next_due_after(task, now: float) -> float | None:
    """The next fire time for a task after `now`, or None if it's a finished one-shot.

    Defensive (returns None on a bad cron expr) so a single malformed task can never
    crash the scheduler loop.
    """
    if task.kind == "cron" and task.cron_expr:
        try:
            return cron_next(task.cron_expr, task.tz, now)
        except Exception:  # noqa: BLE001
            return None
    if task.kind == "every" and task.every_seconds:
        return now + task.every_seconds
    return None  # one-shot "at" -> no next fire

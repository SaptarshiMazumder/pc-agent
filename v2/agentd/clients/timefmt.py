"""WhatsApp-style time labels, shared by the Python clients (terminal REPL + CLI).

One rule set, applied everywhere a 'when' is shown:
  today            -> "14:32"        (a clock time is the most useful 'today' label)
  yesterday        -> "Yesterday"
  within 7 days    -> "Tuesday"      (weekday name)
  same year        -> "5 June"
  older            -> "3 Apr 1996"

Pure functions of (epoch, now) so they're unit-testable without freezing clocks.
The desktop client mirrors these rules in renderer/src/lib/timefmt.ts.
"""

from __future__ import annotations

import time as _time
from datetime import datetime


def _day_delta(dt: datetime, now: datetime) -> int:
    return (now.date() - dt.date()).days


def whatsapp_when(epoch: float, now: float | None = None) -> str:
    """Label for a LIST row (sessions list): time today, else a relative day/date."""
    if not epoch:
        return ""
    dt = datetime.fromtimestamp(epoch)
    ref = datetime.fromtimestamp(now if now is not None else _time.time())
    days = _day_delta(dt, ref)
    if days <= 0:
        return dt.strftime("%H:%M")
    return _day_or_date(dt, ref, days)


def whatsapp_day(epoch: float, now: float | None = None) -> str:
    """Label for a DATE SEPARATOR between messages: 'Today' instead of a clock."""
    if not epoch:
        return ""
    dt = datetime.fromtimestamp(epoch)
    ref = datetime.fromtimestamp(now if now is not None else _time.time())
    days = _day_delta(dt, ref)
    if days <= 0:
        return "Today"
    return _day_or_date(dt, ref, days)


def _day_or_date(dt: datetime, ref: datetime, days: int) -> str:
    if days == 1:
        return "Yesterday"
    if days < 7:
        return dt.strftime("%A")                      # weekday name
    if dt.year == ref.year:
        return f"{dt.day} {dt.strftime('%B')}"        # "5 June"
    return f"{dt.day} {dt.strftime('%b')} {dt.year}"  # "3 Apr 1996"

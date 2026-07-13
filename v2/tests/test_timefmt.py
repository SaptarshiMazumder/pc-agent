"""WhatsApp-style time labels (agentd.clients.timefmt) — the shared display rules."""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.clients.timefmt import whatsapp_day, whatsapp_when

NOW = datetime(2026, 7, 5, 18, 0).timestamp()  # Sunday 5 July 2026, 18:00


def ts(*args):
    return datetime(*args).timestamp()


def test_today_shows_clock_or_today():
    assert whatsapp_when(ts(2026, 7, 5, 14, 32), NOW) == "14:32"
    assert whatsapp_day(ts(2026, 7, 5, 14, 32), NOW) == "Today"


def test_yesterday():
    assert whatsapp_when(ts(2026, 7, 4, 9, 0), NOW) == "Yesterday"
    assert whatsapp_day(ts(2026, 7, 4, 9, 0), NOW) == "Yesterday"


def test_within_week_shows_weekday():
    assert whatsapp_when(ts(2026, 6, 30, 12, 0), NOW) == "Tuesday"


def test_same_year_shows_day_month():
    assert whatsapp_when(ts(2026, 6, 5, 12, 0), NOW) == "5 June"


def test_older_year_shows_full_date():
    assert whatsapp_when(ts(1996, 4, 3, 12, 0), NOW) == "3 Apr 1996"


def test_zero_is_empty():
    assert whatsapp_when(0, NOW) == "" and whatsapp_day(0, NOW) == ""

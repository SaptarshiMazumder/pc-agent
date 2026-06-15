import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clients.terminal.__main__ import resolve_session_choice, sessions_table

SESSIONS = [
    {"sessionId": "term-aaa", "messages": 16, "modified": 1_700_000_000},
    {"sessionId": "term-bbb", "messages": 4, "modified": 1_699_000_000},
]


def test_pick_maps_index_to_session_id():
    assert resolve_session_choice(SESSIONS, "1") == "term-aaa"
    assert resolve_session_choice(SESSIONS, "2") == "term-bbb"


def test_blank_or_invalid_is_cancel():
    assert resolve_session_choice(SESSIONS, "") is None
    assert resolve_session_choice(SESSIONS, "   ") is None
    assert resolve_session_choice(SESSIONS, "abc") is None


def test_out_of_range_is_cancel():
    assert resolve_session_choice(SESSIONS, "0") is None
    assert resolve_session_choice(SESSIONS, "3") is None
    assert resolve_session_choice([], "1") is None


def test_table_builds_and_renders():
    from rich.console import Console

    table = sessions_table(SESSIONS, current="term-bbb")
    assert table.row_count == 2
    assert [c.header for c in table.columns] == ["#", "session", "msgs", "modified"]
    # rendering must not raise and should surface the ids + the current marker
    console = Console(width=100, record=True)
    console.print(table)
    out = console.export_text()
    assert "term-aaa" in out and "term-bbb" in out
    assert "← current" in out

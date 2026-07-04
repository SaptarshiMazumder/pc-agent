import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agentd.clients.terminal.__main__ as term
from agentd.clients.terminal.__main__ import (
    TerminalClient,
    render_plan,
    resolve_session_choice,
    sessions_table,
)


def test_render_plan_checklist_marks_and_text():
    plan = [
        {"step": "find: locate CV", "status": "completed"},
        {"step": "browser: open LinkedIn", "status": "in_progress"},
        {"step": "reply: summarize", "status": "pending"},
    ]
    plain = render_plan(plan).plain
    assert "☒ find: locate CV" in plain          # completed
    assert "☐ browser: open LinkedIn" in plain    # in_progress
    assert "☐ reply: summarize" in plain          # pending
    assert plain.startswith("  ⎿ ")               # Claude-Code-style first row


def test_render_plan_tolerates_unknown_status_and_junk():
    plain = render_plan([{"step": "x", "status": "weird"}, "junk", {"step": "y", "status": "pending"}]).plain
    assert "☐ x" in plain and "☐ y" in plain      # junk entry skipped, unknown -> box

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


def test_welcome_banner_uses_hello_info(monkeypatch):
    from rich.console import Console

    rec = Console(width=100, record=True)
    monkeypatch.setattr(term, "console", rec)
    client = TerminalClient("ws://127.0.0.1:8787", "term-xyz")
    client._print_welcome(
        {
            "agentName": "JARVIS",
            "model": "gemini/gemini-2.5-pro",
            "reasoning": "medium",
            "gatewayUrl": "ws://127.0.0.1:8787",
            "agentId": "main",
            "sessions": 3,
        }
    )
    out = rec.export_text()
    assert "Hi, I'm JARVIS." in out
    assert "gemini/gemini-2.5-pro" in out
    assert "3 saved session(s)" in out
    assert "/sessions" in out and "term-xyz" in out


def test_welcome_banner_tolerates_empty_info(monkeypatch):
    from rich.console import Console

    rec = Console(width=100, record=True)
    monkeypatch.setattr(term, "console", rec)
    TerminalClient("ws://x", "term-1")._print_welcome({})  # must not raise
    assert "Hi, I'm the agent." in rec.export_text()


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

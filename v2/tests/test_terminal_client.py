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
    assert [c.header for c in table.columns] == ["#", "title", "session", "msgs", "when"]
    # rendering must not raise and should surface the ids + the current marker
    console = Console(width=100, record=True)
    console.print(table)
    out = console.export_text()
    assert "term-aaa" in out and "term-bbb" in out
    assert "← current" in out


# ---- desktop-parity commands (RPC plumbing) ----------------------------------------
# These exercise the NON-interactive paths (no TTY in CI, so pickers are skipped): they
# verify the right RPC is called with the right params and that client state updates.

def _client_with_stub(monkeypatch, responses):
    """A TerminalClient whose .request records calls and returns canned payloads."""
    from rich.console import Console

    monkeypatch.setattr(term, "console", Console(width=100, record=True))
    client = TerminalClient("ws://x", "term-1", agent_id="main")
    calls: list = []

    async def fake(method, params=None):
        calls.append((method, params or {}))
        r = responses.get(method, {})
        return r(params) if callable(r) else r

    client.request = fake  # shadow the bound method with the stub
    return client, calls


def test_extra_command_routes_parity_and_ignores_normal(monkeypatch):
    import asyncio

    client, calls = _client_with_stub(monkeypatch, {"config.get": {}})
    # a normal message is never consumed by the parity dispatcher
    assert asyncio.run(client._extra_command("just chatting")) is False
    # a parity command is consumed and hits its RPC
    assert asyncio.run(client._extra_command("/config")) is True
    assert any(m == "config.get" for m, _ in calls)


def test_ws_params_scope_is_project_then_agent():
    client = TerminalClient("ws://x", "s", agent_id="main")
    assert client._ws_params({"path": "a"}) == {"path": "a", "agentId": "main"}
    client.project_id = "proj-x"
    assert client._ws_params({"path": "a"}) == {"path": "a", "projectId": "proj-x"}


def test_session_move_calls_rpc_and_adopts_project(monkeypatch):
    import asyncio

    client, calls = _client_with_stub(
        monkeypatch, {"sessions.move": lambda p: {"ok": True, "projectId": p.get("projectId")}})
    asyncio.run(client._cmd_session(["move", "proj-x"]))
    assert ("sessions.move", {"sessionKey": "term-1", "agentId": "main", "projectId": "proj-x"}) in calls
    assert client.project_id == "proj-x"


def test_session_duplicate_switches_to_the_copy(monkeypatch):
    import asyncio

    client, _ = _client_with_stub(
        monkeypatch, {"sessions.duplicate": {"ok": True, "sessionKey": "term-copy"}})
    asyncio.run(client._cmd_session(["duplicate"]))
    assert client.session_key == "term-copy"


def test_projects_rename_uses_id_and_name(monkeypatch):
    import asyncio

    client, calls = _client_with_stub(monkeypatch, {"projects.rename": {"ok": True}})
    asyncio.run(client._cmd_projects_extra("rename", ["proj-x", "New", "Name"]))
    assert ("projects.rename", {"id": "proj-x", "name": "New Name"}) in calls


def test_config_set_coerces_scalar_and_patches(monkeypatch):
    import asyncio

    client, calls = _client_with_stub(
        monkeypatch, {"config.get": {}, "config.set": {"saved": True}})
    asyncio.run(client._cmd_config(["set", "completeness_check", "true"]))
    assert ("config.set", {"patch": {"completeness_check": True}}) in calls


def test_store_install_calls_marketplace(monkeypatch):
    import asyncio

    client, calls = _client_with_stub(
        monkeypatch, {"marketplace.install": {"installed": True, "id": "figure-creator", "version": "1.0.0"}})
    asyncio.run(client._cmd_store(["install", "figure-creator"]))
    assert ("marketplace.install", {"id": "figure-creator"}) in calls

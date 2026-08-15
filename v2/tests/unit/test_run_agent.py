"""Running an agent must be a TOOL, and it must report what the agent actually did.

Two failures are pinned here.

The first is availability. The skill told the builder to shell out to `agentd ask` — a console
script the wheel declares, so it exists in a packaged install and does not exist in a source
checkout, which is where agents are authored. One real build spent eleven `exec` calls
discovering `python -m agent_runtime.cli.main`, including a try at `import agentd`, a package
name that has not existed for a while. A tool cannot be missing from PATH.

The second is the verdict. An agent that DESCRIBES the work and one that DOES it produce
indistinguishable prose; the list of tools it called is the cheap way to tell them apart, and it
has to survive into the tool's output rather than being summarised away.
"""

from __future__ import annotations

import asyncio

import pytest

from agent_authoring.presentation.run_agent_tool import RunAgentTool
from agent_runtime.clients.one_shot_run import RunOutcome


def _run(tool, params, monkeypatch, outcome: RunOutcome):
    async def fake_run_once(**kwargs):
        fake_run_once.kwargs = kwargs
        return outcome

    monkeypatch.setattr("agent_runtime.clients.one_shot_run.run_once", fake_run_once)
    result = asyncio.run(tool.execute("id", params, asyncio.Event()))
    return result, fake_run_once


def _text(result) -> str:
    return "".join(getattr(b, "text", "") for b in result.content)


@pytest.fixture
def tool():
    return RunAgentTool()


def test_it_reports_the_tools_the_agent_called(tool, monkeypatch):
    """The whole reason this returns more than the reply."""
    outcome = RunOutcome(reply="12.4GB free", tools=["comfy_server", "read"], stop_reason="stop")
    result, _ = _run(tool, {"agent_id": "x", "message": "hi"}, monkeypatch, outcome)

    assert "tools called: comfy_server, read" in _text(result)
    assert not result.is_error


def test_a_run_that_touched_nothing_is_called_out(tool, monkeypatch):
    """A clean run with no tool calls is the commonest way a finished-looking agent turns out to
    be empty, and nothing else announces it — the reply reads perfectly."""
    outcome = RunOutcome(reply="Your costs look fine!", tools=[], stop_reason="stop")
    result, _ = _run(tool, {"agent_id": "x", "message": "what do I owe?"}, monkeypatch, outcome)

    body = _text(result)
    assert "tools called: NONE" in body
    assert "described the work instead of doing it" in body


def test_a_failed_run_is_an_error_result_with_the_reason(tool, monkeypatch):
    """"It failed" and "it failed because your key is missing" are different sentences, and only
    the second one gets fixed."""
    outcome = RunOutcome(reply="", stop_reason="error", error="Missing GEMINI_API_KEY")
    result, _ = _run(tool, {"agent_id": "x", "message": "hi"}, monkeypatch, outcome)

    assert result.is_error
    assert "Missing GEMINI_API_KEY" in _text(result)


def test_not_reaching_the_agent_is_not_the_agents_fault(tool, monkeypatch):
    """A dead daemon reported as a broken agent sends the author to fix working code."""
    outcome = RunOutcome(transport_error="could not start the daemon: no interpreter")
    result, _ = _run(tool, {"agent_id": "x", "message": "hi"}, monkeypatch, outcome)

    assert result.is_error
    assert "could not run x" in _text(result)


def test_each_call_gets_a_fresh_session_by_default(tool, monkeypatch):
    """Reusing one turns "does this agent work?" into "does it work given whatever I asked it
    last time", and those answers differ exactly when it matters."""
    _, spy = _run(tool, {"agent_id": "x", "message": "hi"}, monkeypatch, RunOutcome(reply="ok"))

    assert spy.kwargs["session"] is None


def test_a_session_can_be_named_for_a_deliberate_follow_up(tool, monkeypatch):
    _, spy = _run(
        tool,
        {"agent_id": "x", "message": "and then?", "session": "s1"},
        monkeypatch,
        RunOutcome(reply="ok"),
    )

    assert spy.kwargs["session"] == "s1"


def test_it_refuses_without_a_message(tool, monkeypatch):
    result, _ = _run(tool, {"agent_id": "x"}, monkeypatch, RunOutcome())
    assert result.is_error


def test_the_outcome_knows_when_the_run_was_fine():
    assert RunOutcome(reply="hi", stop_reason="stop").ok
    assert not RunOutcome(stop_reason="error").ok
    assert not RunOutcome(error="boom").ok
    assert not RunOutcome(transport_error="no daemon").ok


def test_the_cli_still_prints_what_it_always_printed(monkeypatch, capsys):
    """`agentd ask` keeps its contract — the run moved, the command did not. Its exit code
    follows the run, because `agentd ask … && something` has to mean what it looks like."""
    from agent_runtime.cli.commands import ask

    async def fake_run_once(**_kwargs):
        return RunOutcome(reply="the answer", tools=["read"], stop_reason="stop")

    monkeypatch.setattr("agent_runtime.clients.one_shot_run.run_once", fake_run_once)
    code = asyncio.run(
        ask._ask(message="hi", agent="x", session=None, url="ws://x", timeout=1.0, quiet=False)
    )

    captured = capsys.readouterr()
    assert code == 0
    assert "the answer" in captured.out
    assert "tools called: read" in captured.err

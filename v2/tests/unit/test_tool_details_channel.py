"""A tool speaks to TWO audiences, and only one of them can read prose.

`content` is for the model. `details` is for a program — a window, a panel, a script. The gateway
used to return only the first, so an app that needed a tool's data had exactly one option: scrape
the message. It was done, with a regex over "3 workflow(s) in C:\\…\\workflows:", and it worked
until a line was reworded — after which the panel rendered "Nothing built yet" over a folder with
two files in it. No error, nothing in the console, nothing to search for.

The other half of the story is the loop guard, which counted "identical calls in a row" with no
notion of time and a counter that lived as long as the daemon. For a no-argument tool that made
every call identical to the last, so the tool worked `limit` times per daemon lifetime and then
refused — the same list, the same panel, a different silent failure.
"""

from __future__ import annotations

import asyncio

import pytest

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.infrastructure.tools.guard import (
    LOOP_WINDOW_SEC,
    GuardedTool,
    ToolPolicy,
)


class Listing(Tool):
    name = "list_things"
    description = "lists"
    parameters = {"type": "object", "properties": {}}
    # A host connection may only invoke tools that declare themselves UI-callable. Declaring it
    # here is what the real caller does too — this is the tier the gate exists for.
    artifact_action = {"mime": ["application/json"], "label": "List", "param": "path"}

    async def execute(self, tool_call_id, params, abort, on_update=None):
        return ToolResult.text(
            "2 thing(s) in C:\\work\\things:\n- a.json  (8 nodes)\n- b.json  (4 nodes)",
            details={"folder": "C:\\work\\things", "things": [{"name": "a.json"}]},
        )


# --------------------------------------------------------------------------- details


def test_a_tool_can_return_structure_alongside_its_prose():
    """Both, not either: the model needs the sentence and the panel needs the list."""
    result = asyncio.run(Listing().execute("id", {}, asyncio.Event()))

    assert "2 thing(s)" in result.content[0].text
    assert result.details["things"] == [{"name": "a.json"}]


def test_the_gateway_passes_details_through():
    """THE MISSING SEAM. Without this a window has only the text, and parsing a message written
    for a reader is how a panel ends up empty over a folder that is full."""
    from agent_runtime.presentation.gateway import Gateway

    gw = Gateway.__new__(Gateway)

    class Service:
        def find_tool(self, name, scope=None):
            return Listing()

    gw.service = Service()
    gw.registry = None
    gw._public_invoke_sem = None

    payload = asyncio.run(gw._tools_invoke({"name": "list_things", "params": {}}))

    assert payload["details"]["things"] == [{"name": "a.json"}]
    assert "2 thing(s)" in payload["text"], "the prose must still be there for the model"


def test_a_tool_without_details_does_not_grow_the_key():
    """Most tools have nothing structured to say. An empty `details` on every result would be a
    field every caller has to check and none can rely on."""
    from agent_runtime.presentation.gateway import Gateway

    class Plain(Tool):
        name = "plain"
        description = ""
        parameters = {"type": "object", "properties": {}}
        artifact_action = {"mime": ["text/plain"], "label": "Do", "param": "path"}

        async def execute(self, tool_call_id, params, abort, on_update=None):
            return ToolResult.text("done")

    gw = Gateway.__new__(Gateway)

    class Service:
        def find_tool(self, name, scope=None):
            return Plain()

    gw.service = Service()
    gw.registry = None
    gw._public_invoke_sem = None

    payload = asyncio.run(gw._tools_invoke({"name": "plain", "params": {}}))

    assert "details" not in payload


# --------------------------------------------------------------------------- loop guard


def _policy(limit: int = 3) -> ToolPolicy:
    return ToolPolicy(
        timeout_sec=None,
        max_retries=0,
        retryable=False,
        retry_on_timeout=False,
        base_backoff_sec=0.0,
        max_backoff_sec=0.0,
        loop_max_repeats=limit,
        loop_warn_after_errors=0,
    )


def _call(tool) -> ToolResult:
    return asyncio.run(tool.execute("id", {}, asyncio.Event()))


def test_a_genuine_spin_is_still_blocked():
    """The guard's whole purpose, unchanged: the same call over and over with nothing between."""
    tool = GuardedTool(Listing(), _policy(limit=3))

    for _ in range(3):
        assert not _call(tool).is_error
    assert _call(tool).is_error, "a runaway loop must still be stopped"


def test_the_same_call_much_later_is_not_a_loop(monkeypatch):
    """A window listing files once per visit, or an agent asking at the start of ten different
    conversations. Both used to be banned as runaway loops — permanently, because the counter
    lived as long as the daemon and a no-argument call is always identical to the last one."""
    import agent_runtime.infrastructure.tools.guard as guard

    clock = [1000.0]
    monkeypatch.setattr(guard.time, "monotonic", lambda: clock[0])
    tool = GuardedTool(Listing(), _policy(limit=3))

    for _ in range(10):
        assert not _call(tool).is_error
        clock[0] += LOOP_WINDOW_SEC + 1  # a visit, a turn, a coffee

    assert not _call(tool).is_error


def test_a_burst_after_a_long_gap_still_trips():
    """Decaying the count must not disarm the guard — a spin that starts an hour in is still a
    spin, and the window is about the gap BETWEEN calls, not about when they began."""
    import agent_runtime.infrastructure.tools.guard as guard

    clock = [1000.0]
    original = guard.time.monotonic
    guard.time.monotonic = lambda: clock[0]  # type: ignore[assignment]
    try:
        tool = GuardedTool(Listing(), _policy(limit=3))
        _call(tool)
        clock[0] += 600  # long idle
        for _ in range(3):
            assert not _call(tool).is_error
            clock[0] += 0.01
        assert _call(tool).is_error
    finally:
        guard.time.monotonic = original  # type: ignore[assignment]


def test_different_arguments_are_never_a_loop():
    class Echo(Tool):
        name = "echo"
        description = ""
        parameters = {"type": "object", "properties": {}}

        async def execute(self, tool_call_id, params, abort, on_update=None):
            return ToolResult.text(str(params))

    tool = GuardedTool(Echo(), _policy(limit=2))
    for i in range(10):
        result = asyncio.run(tool.execute("id", {"n": i}, asyncio.Event()))
        assert not result.is_error

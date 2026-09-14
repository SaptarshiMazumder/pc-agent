from collections import deque

import pytest

from agent_runtime.e2e.checks import run_checks
from agent_runtime.e2e.live_driver import _collect_turn
from agent_runtime.e2e.scenario import Check
from agent_runtime.e2e.trace import Trace, TraceWriter, Turn, _fold_event, load_trace


START = {"type": "tool_execution_start", "toolName": "comfy_install", "toolCallId": "c1", "args": {}}
DETACH = {"type": "job_start", "jobId": "j1", "toolCallId": "c1"}
PROVISIONAL = {"type": "tool_execution_end", "toolCallId": "c1", "result": "continues in background", "isError": False}
END = {"type": "agent_end", "stopReason": "stop"}
DONE = {"type": "job_end", "jobId": "j1", "toolCallId": "c1", "state": "done", "isError": False, "text": "installed"}


def trace(events):
    turn = Turn(index=0, user="install")
    pending = {}
    for event in events:
        _fold_event(turn, event, pending)
    return Trace(turns=[turn])


def passed(value, check):
    return run_checks(value, [Check(check, {"tool": "comfy_install"})])[0].passed


def test_pending_call_is_not_successful():
    value = trace([START])
    assert not passed(value, "tool_succeeded")
    assert not passed(value, "completed")


@pytest.mark.parametrize("reason", ["aborted", "cancelled", "error"])
def test_abort_is_not_completion(reason):
    assert not passed(trace([{**END, "stopReason": reason}]), "completed")


@pytest.mark.parametrize("events", [[START, DETACH, PROVISIONAL, END], [START, PROVISIONAL, DETACH, END]])
def test_provisional_result_is_not_success_in_either_event_order(events):
    value = trace(events)
    assert not passed(value, "tool_succeeded")
    assert not passed(value, "completed")


def test_background_result_replaces_provisional_result():
    value = trace([START, DETACH, PROVISIONAL, END, DONE, {"type": "agent_start"}, END])
    assert passed(value, "tool_succeeded")
    assert passed(value, "completed")
    assert value.all_tools[0].result_text == "installed"


def test_background_failure_is_not_tool_success():
    value = trace([START, DETACH, PROVISIONAL, END, {**DONE, "isError": True, "text": "HTTP 400"}, END])
    assert not passed(value, "tool_succeeded")


def test_trace_cut_after_job_end_is_not_a_completed_conversation():
    assert not passed(trace([START, DETACH, PROVISIONAL, END, DONE]), "completed")


def test_unknown_call_id_cannot_complete_an_unrelated_tool():
    value = trace([START, {**PROVISIONAL, "toolCallId": "different"}, END])
    assert not passed(value, "tool_succeeded")
    assert not passed(value, "completed")


class EventStream:
    def __init__(self, events):
        self.events = deque(events)

    async def next(self, timeout):
        return self.events.popleft() if self.events else None


@pytest.mark.asyncio
async def test_live_driver_waits_for_job_and_followup_turn(tmp_path):
    stream = EventStream([START, DETACH, PROVISIONAL, END, DONE, {"type": "agent_start"},
                          {"type": "message_update", "kind": "text_delta", "text": "verified"}, END])
    path = tmp_path / "trace.jsonl"
    writer = TraceWriter(path)
    writer.open_turn(0, "install")
    try:
        assert await _collect_turn(stream, "test", 0, writer, .01, None)
    finally:
        writer.close()
    assert not stream.events
    result = load_trace(path)
    assert passed(result, "tool_succeeded")
    assert result.turns[0].last_assistant == "verified"


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["cancelled", "lost"])
async def test_live_driver_stops_as_failed_when_background_job_is_lost(tmp_path, state):
    stream = EventStream([START, DETACH, PROVISIONAL, END, {**DONE, "state": state}])
    writer = TraceWriter(tmp_path / "trace.jsonl")
    writer.open_turn(0, "install")
    try:
        assert not await _collect_turn(stream, "test", 0, writer, .01, None)
    finally:
        writer.close()


@pytest.mark.asyncio
async def test_live_driver_does_not_end_at_job_end_alone(tmp_path):
    stream = EventStream([START, DETACH, PROVISIONAL, END, DONE])
    writer = TraceWriter(tmp_path / "trace.jsonl")
    writer.open_turn(0, "install")
    try:
        assert not await _collect_turn(stream, "test", 0, writer, .01, None)
    finally:
        writer.close()

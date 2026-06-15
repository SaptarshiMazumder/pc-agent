import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.infrastructure.engine.incomplete_turn import (
    classify_incomplete_turn,
    is_empty_response,
    is_planning_only,
    is_reasoning_only,
    resolve_max_run_loop_iterations,
)
from agentd.domain.messages import AssistantMessage, TextContent, ThinkingContent, ToolCallContent


def assistant(text="", thinking="", tool_calls=None, stop_reason="stop"):
    content = []
    if thinking:
        content.append(ThinkingContent(thinking=thinking))
    if text:
        content.append(TextContent(text=text))
    if tool_calls:
        for tc in tool_calls:
            content.append(ToolCallContent(id=tc, name="x", arguments={}))
    return AssistantMessage(content=content, stop_reason=stop_reason)


def test_planning_only_detected():
    m = assistant("I'll search for the jobs and read the listings to find matches.")
    assert is_planning_only(m)
    assert classify_incomplete_turn(m) == "planning_only"


def test_planning_only_not_triggered_on_real_answer():
    # delivers results (URL + "here are") -> not planning-only
    m = assistant("Here are the jobs I found: https://example.com/job1 and one more.")
    assert not is_planning_only(m)
    assert classify_incomplete_turn(m) is None


def test_planning_only_not_triggered_without_action_verb():
    m = assistant("I'll think about that.")
    assert not is_planning_only(m)


def test_planning_only_skips_when_tool_called():
    m = assistant("I'll search now.", tool_calls=["tc1"])
    assert not is_planning_only(m)


def test_planning_only_skips_long_text():
    m = assistant("I will run the analysis. " + "x" * 800)
    assert not is_planning_only(m)


def test_reasoning_only_detected():
    m = assistant(thinking="lots of reasoning here", text="")
    assert is_reasoning_only(m)
    assert classify_incomplete_turn(m) == "reasoning_only"


def test_reasoning_only_not_when_visible_text():
    m = assistant(thinking="reasoning", text="the answer")
    assert not is_reasoning_only(m)


def test_empty_response_detected():
    m = assistant(text="", thinking="", stop_reason="stop")
    assert is_empty_response(m)
    assert classify_incomplete_turn(m) == "empty_response"


def test_empty_response_not_on_error_stop():
    m = assistant(text="", stop_reason="error")
    assert not is_empty_response(m)


def test_iteration_cap_formula():
    assert resolve_max_run_loop_iterations(1) == 32  # 24 + 8 = 32 -> max(32, 32)
    assert resolve_max_run_loop_iterations(20) == 160  # 24 + 160 = 184 -> min(160)

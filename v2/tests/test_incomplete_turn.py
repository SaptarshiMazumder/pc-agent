import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.domain.messages import AssistantMessage, TextContent, ThinkingContent, ToolCallContent
from agentd.infrastructure.engine.incomplete_turn import (
    PlanningContext,
    classify_incomplete_turn,
    is_empty_response,
    is_likely_actionable_user_prompt,
    is_planning_only,
    is_reasoning_only,
    resolve_max_run_loop_iterations,
    should_apply_planning_only_guard,
)


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


# --- OpenClaw firing guards (the gates our first port dropped) ---------------

_ACT = "I'll search the listings and read them to find matches."


def test_planning_only_gated_off_for_non_agentic_model():
    # regex matches, but a non-agentic model (deepseek) gets no planning-only nudge
    m = assistant(_ACT)
    assert is_planning_only(m)
    ctx = PlanningContext(user_prompt="can you find jobs?", model="deepseek/deepseek-v4-pro")
    assert classify_incomplete_turn(m, ctx) is None


def test_planning_only_fires_for_gemini_with_actionable_prompt():
    m = assistant(_ACT)
    ctx = PlanningContext(user_prompt="can you find me jobs?", model="gemini/gemini-2.5-flash")
    assert classify_incomplete_turn(m, ctx) == "planning_only"


def test_planning_only_fires_for_strict_agentic_contract():
    m = assistant(_ACT)
    ctx = PlanningContext(
        user_prompt="find the jobs", model="anything", execution_contract="strict-agentic"
    )
    assert classify_incomplete_turn(m, ctx) == "planning_only"


def test_planning_only_gated_off_for_non_actionable_prompt():
    # agentic model, but the user said nothing to act on -> no nudge
    m = assistant(_ACT)
    ctx = PlanningContext(user_prompt="thanks, that's all for now", model="gemini/gemini-2.5-flash")
    assert classify_incomplete_turn(m, ctx) is None


def test_planning_only_legacy_regex_only_without_ctx():
    # no PlanningContext (legacy callers/tests) -> regex alone decides, unchanged
    assert classify_incomplete_turn(assistant(_ACT)) == "planning_only"


def test_completion_regex_excludes_blocker_statement():
    # 'the blocker is' marks the turn as delivering a real blocker, not just planning
    assert not is_planning_only(assistant("I'll check the kitchen but the blocker is it's closed."))


def test_conversational_handoff_on_non_agentic_model_is_not_nudged():
    # the sakana case: a warm reply that hands back to the user, on a non-Gemini agent
    reply = (
        "Sorry, we're closed Mondays. Would you like another day? "
        "Let me know a date and I'll check availability for you!"
    )
    ctx = PlanningContext(
        user_prompt="can u book a table for 2 on monday?", model="deepseek/deepseek-v4-pro"
    )
    assert classify_incomplete_turn(assistant(reply), ctx) is None


def test_model_guard_helpers():
    assert should_apply_planning_only_guard("gemini/gemini-3.1-pro-preview")
    assert should_apply_planning_only_guard("gemini-2.5-flash")  # bare gemini id
    assert not should_apply_planning_only_guard("deepseek/deepseek-v4-pro")
    assert not should_apply_planning_only_guard("openai/gpt-4o")
    assert should_apply_planning_only_guard("deepseek/deepseek-v4-pro", "strict-agentic")


def test_actionable_prompt_helper():
    assert is_likely_actionable_user_prompt("can you make a reservation")
    assert is_likely_actionable_user_prompt("is there availability tonight?")
    assert is_likely_actionable_user_prompt("go ahead")  # multilingual ack set
    assert is_likely_actionable_user_prompt("やって")
    assert not is_likely_actionable_user_prompt("thanks so much")
    assert not is_likely_actionable_user_prompt("")

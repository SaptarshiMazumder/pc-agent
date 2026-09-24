"""`tool_says`: a check over what the agent PUT IN a tool call — the ask panel's rows, an
emitted graph's node classes — which no prose check can see."""

from agent_runtime.e2e.checks import run_checks, vocabulary
from agent_runtime.e2e.scenario import Check
from agent_runtime.e2e.trace import ToolCall, Trace, Turn


def _trace() -> Trace:
    t = Trace()
    turn = Turn(index=0, user="switch it to kling")
    turn.tools.append(ToolCall(
        name="ask_user",
        args={"services": [{"name": "Kling 3.0 (kling-v3)", "credits": 307152, "usd": 1.84}]},
        ok=True, completed=True,
    ))
    turn.tools.append(ToolCall(
        name="comfy_emit",
        args={"nodes": [{"class_type": "KlingVideoNode", "inputs": {"image": "@model"}}]},
        ok=True, completed=True,
    ))
    turn.ended = True
    t.turns.append(turn)
    return t


def _one(name: str, **args):
    return run_checks(_trace(), [Check(name=name, args=args)])[0]


def test_matches_the_arguments_as_sorted_json():
    r = _one("tool_says", tool="ask_user", pattern=r'"credits": [1-9][0-9]*,\s*"name": "[^"]*kling')
    assert r.passed, r.detail
    assert "ask_user call(s) [1] matched" in r.detail


def test_reads_nested_structures_and_is_case_insensitive():
    assert _one("tool_says", tool="comfy_emit", pattern="klingvideonode").passed
    assert _one("tool_says", tool="comfy_emit", pattern="@model").passed


def test_fails_when_no_call_matches_or_the_tool_never_ran():
    r = _one("tool_says", tool="comfy_emit", pattern="Seedance")
    assert not r.passed and "none of 1 comfy_emit call(s) matched" in r.detail
    r = _one("tool_says", tool="comfy_run", pattern=".")
    assert not r.passed and "never ran" in r.detail


def test_requires_both_arguments_and_a_valid_pattern():
    assert not _one("tool_says", tool="ask_user").passed
    assert not _one("tool_says", pattern="x").passed
    r = _one("tool_says", tool="ask_user", pattern="(")
    assert not r.passed and "bad pattern" in r.detail


def test_is_in_the_authorable_vocabulary():
    entry = next(v for v in vocabulary() if v["name"] == "tool_says")
    assert set(entry["args"]) == {"tool", "pattern"}

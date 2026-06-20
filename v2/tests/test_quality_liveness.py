"""Tests for the decoupled liveness observers (#3 CallRateBrake, #4 NoProgressDetector),
the LLM-judge used by the verify_answer tool, and the completeness prompt section (#1).
(Answer verification itself is a tool — see test_verify_tool.py.)
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.application.interfaces.verifier import VerifyContext
from agentd.domain.events import AgentEvent
from agentd.domain.messages import AssistantMessage, TextContent, ToolCallContent, UserMessage
from agentd.infrastructure.engine.native import run_agent_loop
from agentd.infrastructure.tools import Tool, ToolResult


# --- minimal loop harness (mirrors test_loop.py) ---------------------------
class EchoTool(Tool):
    name = "echo"
    description = "echo"
    label = "Echo"
    parameters = {"type": "object", "properties": {"text": {"type": "string"}}}

    async def execute(self, tool_call_id, params, abort, on_update=None):
        return ToolResult.text(f"echo: {params.get('text', '')}")


def _stream(script):
    turns = list(script)

    async def stream_fn(*, model, system_prompt, messages, tools, abort):
        for ev in turns.pop(0):
            yield ev

    return stream_fn


def _text(t):
    return [{"type": "text_delta", "delta": t},
            {"type": "done", "message": AssistantMessage(content=[TextContent(text=t)], stop_reason="stop")}]


def _tool(call_id, text="x"):
    b = ToolCallContent(id=call_id, name="echo", arguments={"text": text})
    return [{"type": "toolcall_end", "toolCall": {"id": b.id, "name": b.name}},
            {"type": "done", "message": AssistantMessage(content=[b], stop_reason="toolUse")}]


async def _run(script, *, observers=None, tools=None):
    events = []

    async def on_event(ev: AgentEvent):
        events.append(ev)

    msgs = [UserMessage(content="go")]
    await run_agent_loop(
        messages=msgs, system_prompt="sys", tools=tools or [EchoTool()],
        stream_fn=_stream(script), model="fake", on_event=on_event, abort=asyncio.Event(),
        observers=observers or [],
    )
    return events, msgs


# --- #3 CallRateBrake -------------------------------------------------------
@pytest.mark.asyncio
async def test_call_rate_brake_halts_repeated_tool():
    from agentd.infrastructure.liveness import CallRateBrake

    script = [_tool(f"c{i}") for i in range(4)] + [_text("done")]
    events, msgs = await _run(script, observers=[CallRateBrake(window=10, max_per_tool=3)])
    stuck = [e for e in events if e.type == "continuation" and e.payload["reason"] == "stuck"]
    assert stuck, "brake should fire a stuck continuation"
    assert any(m.role == "user" and m.content.startswith("[liveness]") for m in msgs)
    assert msgs[-1].text == "done"


@pytest.mark.asyncio
async def test_call_rate_brake_off_by_default():
    script = [_tool(f"c{i}") for i in range(6)] + [_text("done")]
    events, msgs = await _run(script)  # no observers
    assert not [e for e in events if e.type == "continuation" and e.payload["reason"] == "stuck"]


# --- #4 NoProgressDetector --------------------------------------------------
@pytest.mark.asyncio
async def test_no_progress_halts_on_repeated_results():
    from agentd.infrastructure.liveness import NoProgressDetector

    script = [_tool("a", "same"), _tool("b", "same"), _tool("c", "same"), _text("done")]
    events, msgs = await _run(script, observers=[NoProgressDetector(max_idle_turns=2)])
    stuck = [e for e in events if e.type == "continuation" and e.payload["reason"] == "stuck"]
    assert stuck, "no-progress should fire after repeated results"
    assert any(m.role == "user" and m.content.startswith("[liveness]") for m in msgs)


# --- the LLM-judge (used by the verify_answer tool) ------------------------
@pytest.mark.asyncio
async def test_llm_judge_parses_fail_verdict():
    from agentd.infrastructure.verify import LlmJudgeVerifier

    async def judge(_):
        return '{"ok": false, "reasons": "only 3 of 5 found"}'
    v = await LlmJudgeVerifier(judge).verify(VerifyContext(task="5 items", answer="3"))
    assert v.ok is False and "3 of 5" in v.reasons


@pytest.mark.asyncio
async def test_llm_judge_extracts_json_from_chatter():
    from agentd.infrastructure.verify import LlmJudgeVerifier

    async def judge(_):
        return 'Sure! {"ok": true, "reasons": ""} hope that helps'
    assert (await LlmJudgeVerifier(judge).verify(VerifyContext(task="t", answer="a"))).ok is True


@pytest.mark.asyncio
async def test_llm_judge_fail_open_on_garbage_and_error():
    from agentd.infrastructure.verify import LlmJudgeVerifier

    async def garbage(_):
        return "no json here"
    async def boom(_):
        raise RuntimeError("judge down")
    assert (await LlmJudgeVerifier(garbage).verify(VerifyContext(task="t", answer="a"))).ok is True
    assert (await LlmJudgeVerifier(boom).verify(VerifyContext(task="t", answer="a"))).ok is True


# --- factories + prompt -----------------------------------------------------
def test_build_observers_default_and_selection():
    from agentd.infrastructure.liveness import build_observers
    assert build_observers(SimpleNamespace(liveness=None)) == []
    obs = build_observers(SimpleNamespace(liveness=["callrate", "noprogress", "bogus"]))
    assert [type(o).__name__ for o in obs] == ["CallRateBrake", "NoProgressDetector"]


def test_completeness_section_toggle():
    from agentd.config import load_config
    from agentd.infrastructure.prompt import build_system_prompt
    cfg = load_config()
    cfg.completeness_check = False
    assert "Before You Finish" not in build_system_prompt(cfg, [], cfg.model, "medium", skills=[])
    cfg.completeness_check = True
    assert "## Before You Finish" in build_system_prompt(cfg, [], cfg.model, "medium", skills=[])

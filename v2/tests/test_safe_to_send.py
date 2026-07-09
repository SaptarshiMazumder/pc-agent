"""Safe-to-send egress gate: an independent judge clears or blocks a channel reply against
the agent's own rules; the gateway substitutes a safe reply when blocked. FAIL-CLOSED."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.application.interfaces.safe_to_send import SafeToSendContext, SafeToSendVerdict
from agentd.infrastructure.safe_to_send import build_safe_to_send_gate
from agentd.infrastructure.safe_to_send.gate import DEFAULT_SAFE_REPLY, LlmSafeToSendGate


def _ctx(answer="Today: 0 reservations, 12 tables total.", conversation=""):
    return SafeToSendContext(
        audience="external",
        policy="NEVER share other customers' info or business totals with a customer.",
        question="tell me about reservation status",
        conversation=conversation,
        answer=answer,
    )


def _gate(judge):
    return LlmSafeToSendGate(judge)


# ── gate verdicts ───────────────────────────────────────────────────────────


def test_safe_reply_passes():
    async def judge(_):
        return '{"safe": true}'

    v = asyncio.run(_gate(judge).check(_ctx()))
    assert v.safe and v.safe_reply == ""


def test_unsafe_reply_blocked_with_safe_replacement():
    async def judge(_):
        return (
            '{"safe": false, "reason": "leaks business totals", "safe_reply": "Whats your name?"}'
        )

    v = asyncio.run(_gate(judge).check(_ctx()))
    assert not v.safe and v.reason == "leaks business totals" and v.safe_reply == "Whats your name?"


def test_blocked_without_safe_reply_uses_default():
    async def judge(_):
        return '{"safe": false, "reason": "leak"}'

    v = asyncio.run(_gate(judge).check(_ctx()))
    assert not v.safe and v.safe_reply == DEFAULT_SAFE_REPLY


def test_fail_closed_on_judge_error():
    async def judge(_):
        raise RuntimeError("model down")

    v = asyncio.run(_gate(judge).check(_ctx()))
    assert not v.safe and v.safe_reply == DEFAULT_SAFE_REPLY  # error => BLOCK, not leak


def test_fail_closed_on_unparseable_output():
    async def judge(_):
        return "yeah that's fine to send"

    v = asyncio.run(_gate(judge).check(_ctx()))
    assert not v.safe  # can't parse => BLOCK


def test_json_embedded_in_prose_is_parsed():
    async def judge(_):
        return 'Here is my call: {"safe": false, "reason": "x"} — done.'

    v = asyncio.run(_gate(judge).check(_ctx()))
    assert not v.safe


def test_prompt_contains_policy_question_answer_and_conversation():
    seen = {}

    async def judge(prompt):
        seen["p"] = prompt
        return '{"safe": true}'

    asyncio.run(
        _gate(judge).check(
            _ctx(answer="SECRET-12-TABLES", conversation="Customer: hi, this is Sap")
        )
    )
    p = seen["p"]
    assert "NEVER share other customers' info" in p  # the agent's own rules
    assert "SECRET-12-TABLES" in p  # the reply under review
    assert "reservation status" in p  # what was asked
    assert "this is Sap" in p  # the recent conversation


# ── factory ─────────────────────────────────────────────────────────────────


def test_factory_returns_none_when_disabled():
    class C:
        safe_to_send_check = False

    assert build_safe_to_send_gate(C()) is None


def test_factory_builds_when_enabled_with_a_model():
    class C:
        safe_to_send_check = True
        safe_to_send_model = None
        verify_model = None
        search_model = "gemini/gemini-2.5-flash"
        model = "x"

    assert build_safe_to_send_gate(C()) is not None


def test_factory_returns_none_without_any_model():
    class C:
        safe_to_send_check = True
        safe_to_send_model = None
        verify_model = None
        search_model = None
        model = None

    assert build_safe_to_send_gate(C()) is None  # fail-SAFE: no model => no gate


# ── gateway egress integration (_verify_safe_to_send) ───────────────────────


def _gateway(gate, audience="external"):
    from agentd.presentation.gateway import Gateway

    class _Spec:
        instructions = "NEVER reveal other customers' info."
        audience = ""

    _Spec.audience = audience

    class _Reg:
        def get(self, _aid):
            return _Spec()

    return Gateway(config=object(), service=object(), registry=_Reg(), safe_to_send_gate=gate)


def _handle():
    from agentd.presentation.gateway import RunHandle

    return RunHandle(run_id="r1", session_key="agent:sakana-sushi:line:U1", abort=asyncio.Event())


def _send(gw, question, answer):
    return asyncio.run(gw._verify_safe_to_send(_handle(), "sakana-sushi", question, answer))


def test_external_agent_blocks_and_substitutes_safe_reply():
    class Gate:
        async def check(self, ctx):
            assert ctx.audience == "external"
            assert "other customers" in ctx.policy  # pulled from the agent's rules
            return SafeToSendVerdict(safe=False, reason="leak", safe_reply="May I have your name?")

    out = _send(_gateway(Gate()), "status?", "0 today, 12 tables")
    assert out == "May I have your name?"


def test_external_agent_allows_passthrough_when_safe():
    class Gate:
        async def check(self, _ctx):
            return SafeToSendVerdict(safe=True)

    out = _send(_gateway(Gate()), "hi", "Hello! How can I help?")
    assert out == "Hello! How can I help?"


def test_no_gate_is_passthrough():
    out = _send(_gateway(None), "hi", "anything")
    assert out == "anything"


def test_internal_agent_is_not_gated():
    calls = []

    class Gate:
        async def check(self, _ctx):
            calls.append(1)  # must NEVER run for a non-external agent
            return SafeToSendVerdict(safe=False, reason="x", safe_reply="blocked")

    out = _send(_gateway(Gate(), audience="internal"), "status?", "0 today, 12 tables")
    assert out == "0 today, 12 tables"  # not external -> raw answer passes
    assert calls == []  # the judge was never even invoked


def test_unset_audience_is_not_gated():
    calls = []

    class Gate:
        async def check(self, _ctx):
            calls.append(1)
            return SafeToSendVerdict(safe=False, reason="x", safe_reply="blocked")

    out = _send(_gateway(Gate(), audience=""), "q", "raw")
    assert out == "raw" and calls == []  # absent audience -> not gated

"""A run must never fail quietly.

The bug these pin down: `openai/gpt-5` returned "no credits remaining" on every call. The
runtime failed over to a weaker model, logged one WARNING, that model returned reasoning
only three times, and the run ended reporting `stopReason: "stop"` with the text "couldn't
generate a response, please try again". Three layers of silence turned a billing problem
into days of retrying.

Each layer gets a test:
  * failover EMITS an event, it does not just log
  * the engine turns that into `model_fallback` for every client
  * an empty run ends `no_output`, and says WHY, naming the fallback
  * model_trace reports who ANSWERED, not who was asked
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_runtime.domain.messages import AssistantMessage
from agent_runtime.infrastructure.engine.incomplete_turn import (
    INCOMPLETE_TURN_FALLBACK_TEXT,
    describe_empty_run,
)
from agent_runtime.infrastructure.llm.failover import make_failover_stream


def _drain(stream, **kw):
    async def go():
        return [ev async for ev in stream(**kw)]

    return asyncio.run(go())


def _stream_of(*scripted):
    """An inner stream whose Nth call yields the Nth scripted sequence."""
    calls = {"n": 0}

    async def inner(*, model, system_prompt, messages, tools, abort):
        seq = scripted[min(calls["n"], len(scripted) - 1)]
        calls["n"] += 1
        for ev in seq:
            yield ev

    inner.calls = calls
    return inner


def _errored(msg="RateLimitError: no credits remaining"):
    return {"type": "done", "message": AssistantMessage(stop_reason="error", error_message=msg)}


def _ok(text="hello"):
    return {"type": "done", "message": AssistantMessage(stop_reason="stop")}


ARGS = dict(system_prompt="", messages=[], tools=[], abort=None)


# --- failover announces itself ------------------------------------------------
def test_failover_emits_an_event_not_just_a_log():
    inner = _stream_of([_errored()], [_ok()])
    stream = make_failover_stream(inner, ["gemini/gemini-2.5-flash"])
    events = _drain(stream, model="openai/gpt-5", **ARGS)

    fb = [e for e in events if e.get("type") == "fallback"]
    assert len(fb) == 1, "a silent failover is the bug"
    assert fb[0]["from"] == "openai/gpt-5"
    assert fb[0]["to"] == "gemini/gemini-2.5-flash"
    assert "no credits" in fb[0]["reason"]


def test_failover_event_precedes_the_answer():
    """The warning has to arrive before the reply it is warning about."""
    inner = _stream_of([_errored()], [{"type": "text_delta", "delta": "hi"}, _ok()])
    stream = make_failover_stream(inner, ["backup/model"])
    kinds = [e["type"] for e in _drain(stream, model="primary/model", **ARGS)]
    assert kinds.index("fallback") < kinds.index("text_delta")


def test_no_fallback_configured_is_unchanged():
    """Opt-in stays opt-in: with no fallbacks the inner stream is returned untouched."""
    inner = _stream_of([_errored()])
    assert make_failover_stream(inner, []) is inner
    assert make_failover_stream(inner, None) is inner


def test_error_after_output_is_not_retried():
    """Already-streamed output means retrying would duplicate it — the error passes through
    and no fallback event is invented."""
    inner = _stream_of([{"type": "text_delta", "delta": "partial"}, _errored()], [_ok()])
    stream = make_failover_stream(inner, ["backup/model"])
    events = _drain(stream, model="primary/model", **ARGS)
    assert not [e for e in events if e.get("type") == "fallback"]
    assert events[-1]["message"].stop_reason == "error"


# --- the empty-run diagnosis --------------------------------------------------
def test_empty_run_names_the_failure_and_the_fallback():
    text = describe_empty_run("reasoning_only", ("openai/gpt-5", "gemini/gemini-2.5-flash"))
    assert "reasoning" in text
    assert "openai/gpt-5" in text and "gemini/gemini-2.5-flash" in text
    assert "not equivalent" in text


def test_empty_run_says_retrying_will_not_help():
    """The old text was 'Please try again' — advice to repeat a deterministic failure."""
    assert "Please try again" not in INCOMPLETE_TURN_FALLBACK_TEXT
    for kind in ("reasoning_only", "planning_only", "empty_response"):
        assert "fail the same way" in describe_empty_run(kind, None)


def test_empty_run_without_a_diagnosis_says_nothing_rather_than_guessing():
    assert describe_empty_run(None, None) == ""


def test_fallback_alone_is_still_reported():
    """No classified retry kind, but a model swap happened — that alone explains a lot."""
    text = describe_empty_run(None, ("a/model", "b/model"))
    assert "a/model" in text and "b/model" in text

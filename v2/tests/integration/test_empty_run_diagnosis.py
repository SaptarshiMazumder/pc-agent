"""Replay the real failure through the actual run loop.

The reproduction, exactly as it happened: the configured model errors before output, failover
puts a weaker model in charge, and that model returns reasoning-only on every attempt until
the retry budget is gone. The run must NOT report success, and the closing message must name
both causes — the failure mode and the model swap.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_runtime.domain.events import AgentEvent
from agent_runtime.domain.messages import (
    AssistantMessage,
    TextContent,
    ThinkingContent,
    UserMessage,
)
from agent_runtime.infrastructure.engine.native import run_agent_loop
from agent_runtime.infrastructure.llm.failover import make_failover_stream

THINKING = "**Analyzing Resume**\n\nI'll use the `read_document` tool to extract the skills."


def _run(stream_fn, model="openai/gpt-5", **kw):
    events: list[AgentEvent] = []

    async def on_event(ev):
        events.append(ev)

    asyncio.run(
        run_agent_loop(
            messages=[UserMessage(content="here's my resume, find me jobs")],
            system_prompt="",
            tools=[],
            stream_fn=stream_fn,
            model=model,
            on_event=on_event,
            abort=asyncio.Event(),
            **kw,
        )
    )
    return events


def _find(events, type_):
    return [e for e in events if e.type == type_]


def _reasoning_only_forever():
    """Every call: some thinking, then a normal stop. No text, no tool call."""

    async def inner(*, model, system_prompt, messages, tools, abort):
        yield {"type": "thinking_delta", "delta": THINKING}
        yield {
            "type": "done",
            "message": AssistantMessage(
                content=[ThinkingContent(thinking=THINKING)], stop_reason="stop", model=model
            ),
        }

    return inner


def test_reasoning_only_run_does_not_report_success():
    """It ended with nothing to show — `stop` would be a lie, and it is what made this look
    like a hang rather than a failure."""
    events = _run(_reasoning_only_forever())
    end = _find(events, "agent_end")[-1]
    assert end.payload["stopReason"] == "no_output"


def test_reasoning_only_run_explains_itself():
    events = _run(_reasoning_only_forever())
    text = "".join(
        c.get("text", "")
        for c in (_find(events, "message_end")[-1].payload["message"].get("content") or [])
    )
    assert "reasoning" in text.lower()
    assert "fail the same way" in text
    assert "Please try again" not in text  # the old shrug


def test_the_full_original_failure_names_the_dead_model():
    """Primary errors -> failover -> reasoning-only. Both facts must reach the user."""
    calls = {"n": 0}

    async def inner(*, model, system_prompt, messages, tools, abort):
        calls["n"] += 1
        if model == "openai/gpt-5":
            yield {
                "type": "done",
                "message": AssistantMessage(
                    stop_reason="error",
                    error_message="RateLimitError: You have no credits remaining.",
                ),
            }
            return
        yield {"type": "thinking_delta", "delta": THINKING}
        yield {
            "type": "done",
            "message": AssistantMessage(
                content=[ThinkingContent(thinking=THINKING)], stop_reason="stop", model=model
            ),
        }

    events = _run(make_failover_stream(inner, ["gemini/gemini-2.5-flash"]))

    fb = _find(events, "model_fallback")
    assert fb, "the user must be told their model was swapped out"
    assert fb[0].payload["from"] == "openai/gpt-5"
    assert fb[0].payload["to"] == "gemini/gemini-2.5-flash"
    assert "no credits" in fb[0].payload["reason"]

    text = "".join(
        c.get("text", "")
        for c in (_find(events, "message_end")[-1].payload["message"].get("content") or [])
    )
    assert "openai/gpt-5" in text and "gemini/gemini-2.5-flash" in text
    assert _find(events, "agent_end")[-1].payload["stopReason"] == "no_output"


def test_model_trace_reports_who_answered_not_who_was_asked():
    async def inner(*, model, system_prompt, messages, tools, abort):
        yield {"type": "text_delta", "delta": "done"}
        yield {
            "type": "done",
            "message": AssistantMessage(
                content=[TextContent(text="done")],
                stop_reason="stop",
                model="gemini/gemini-2.5-flash",
            ),
        }

    events = _run(inner, model="openai/gpt-5", model_trace=True)
    trace = _find(events, "model_trace")[0].payload
    assert trace["model"] == "gemini/gemini-2.5-flash"   # who ANSWERED
    assert trace["requestedModel"] == "openai/gpt-5"      # who we ASKED


def test_a_normal_answer_still_ends_stop():
    """The honest ending must not fire on healthy runs."""

    async def inner(*, model, system_prompt, messages, tools, abort):
        yield {"type": "text_delta", "delta": "here are your jobs"}
        yield {
            "type": "done",
            "message": AssistantMessage(content=[TextContent(text="here are your jobs")],
                             stop_reason="stop", model=model),
        }

    events = _run(inner)
    assert _find(events, "agent_end")[-1].payload["stopReason"] == "stop"
    assert not _find(events, "model_fallback")


def test_a_specific_stop_reason_is_never_downgraded():
    """`error` (and `length`, …) already name a precise cause. Replacing them with the vaguer
    `no_output` would trade a diagnosis for a symptom — only the success-claiming `stop` is
    eligible for replacement."""

    async def inner(*, model, system_prompt, messages, tools, abort):
        yield {
            "type": "done",
            "message": AssistantMessage(
                stop_reason="error", error_message="provider exploded", model=model
            ),
        }

    events = _run(inner)
    assert _find(events, "agent_end")[-1].payload["stopReason"] == "error"


def test_the_diagnosis_is_STREAMED_so_clients_actually_render_it():
    """Clients build the transcript from `text_delta`; `message_end` only closes the streaming
    state (clients/ui store.ts). A diagnosis announced solely via message_end is persisted,
    logged — and invisible on screen, which is the exact silence this change exists to end."""
    events = _run(_reasoning_only_forever())
    deltas = "".join(
        e.payload.get("delta", "")
        for e in events
        if e.type == "message_update" and e.payload.get("kind") == "text_delta"
    )
    assert "reasoning" in deltas.lower(), "the diagnosis never reached a delta-based client"
    assert "fail the same way" in deltas


def test_a_second_run_in_a_poisoned_session_still_nudges():
    """END TO END: a session already carrying retry nudges from an earlier run must still get
    planning-only recovery. Before the fix the nudges counted as the user's words, so the
    guard concluded nobody had asked for anything and the recovery layer stayed off for the
    rest of the session — the model promised to act, stopped, and the user had to prod it."""
    from agent_runtime.infrastructure.engine.incomplete_turn import RETRY_INSTRUCTIONS

    PROMISE = (
        "I have successfully read your resume. Now I will proceed to search for suitable job "
        "postings on LinkedIn based on your skills and experience as a FullStack Engineer."
    )

    async def inner(*, model, system_prompt, messages, tools, abort):
        yield {"type": "text_delta", "delta": PROMISE}
        yield {
            "type": "done",
            "message": AssistantMessage(
                content=[TextContent(text=PROMISE)], stop_reason="stop", model=model
            ),
        }

    events = []

    async def on_event(ev):
        events.append(ev)

    asyncio.run(
        run_agent_loop(
            # the real shape: the request, then nudges persisted by earlier failed runs
            messages=[
                UserMessage(content="okay, so heres my resume. find me suitable jobs"),
                UserMessage(content=RETRY_INSTRUCTIONS["reasoning_only"]),
                UserMessage(content=RETRY_INSTRUCTIONS["empty_response"]),
            ],
            system_prompt="",
            tools=[],
            stream_fn=inner,
            model="gemini/gemini-3.1-pro-preview",
            on_event=on_event,
            abort=asyncio.Event(),
        )
    )
    nudges = [e for e in events if e.type == "continuation"]
    assert nudges, "the recovery layer stayed off — the nudges were read as the user's words"
    assert nudges[0].payload["reason"] == "planning_only"

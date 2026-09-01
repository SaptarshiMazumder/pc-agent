"""How full is this conversation? — the number that explains a failure nobody can currently see.

A conversation that outgrows its model does not fail loudly. The provider returns an EMPTY
response; the incomplete-turn retry appends another message and re-sends; the user watches the
same "couldn't generate a response" twice with nothing on screen to explain it. The number that
would have explained it — how much of the window is used — is knowable the entire time.

The used half is EXACT and is not estimated here: every assistant message carries the tokens the
provider actually billed. This suite pins that we report it, that we only report it when both
halves are genuinely known, and that an unknown model produces no meter rather than a wrong one.
"""

from __future__ import annotations

import asyncio

import pytest

from agent_runtime.domain.events import APP_FACING_EVENTS
from agent_runtime.infrastructure.llm.context_limits import max_input_tokens


# ── the denominator ─────────────────────────────────────────────────────────
def test_a_known_model_reports_its_input_budget():
    assert max_input_tokens("gpt-4o") == 128_000


def test_a_routing_prefix_does_not_hide_the_model():
    """THE CASE THAT MATTERS FOR EVERY HOSTED INSTALL. Model calls are routed through a LiteLLM
    proxy, so the runtime's model id is `litellm_proxy/<model>` — which is not a row in any table.
    Reading only the full string would leave every hosted deployment with no meter at all."""
    assert max_input_tokens("litellm_proxy/gpt-4o") == 128_000
    assert max_input_tokens("openrouter/gpt-4o") == 128_000


def test_an_unknown_model_says_it_does_not_know():
    """None, never a guess and never 0. A guessed denominator shows a full meter on an empty
    chat, and 0 would read as 'no room at all' — both are worse than showing nothing."""
    assert max_input_tokens("a-model-nobody-has-heard-of") is None
    assert max_input_tokens("") is None


def test_the_answer_is_cached_per_model():
    """Asked once per assistant message, on a hot path, and the answer cannot change while the
    process lives."""
    first = max_input_tokens("gpt-4o")
    assert max_input_tokens("gpt-4o") == first


def test_max_tokens_is_not_mistaken_for_the_window():
    """On many rows `max_tokens` means max OUTPUT. Reading it as the context window would
    understate a 128k model as an 8k one — and the meter would sit at 'full' permanently."""
    import litellm

    row = litellm.model_cost.get("gpt-4o") or {}
    assert row.get("max_tokens") != row.get("max_input_tokens"), (
        "this test is only meaningful while the two differ for this model"
    )
    assert max_input_tokens("gpt-4o") == row["max_input_tokens"]


# ── the event ───────────────────────────────────────────────────────────────
def test_context_usage_is_an_app_facing_event():
    """A window cannot warn about a wall it is not told about. It must be in the vocabulary the
    UI rules validate against, or an agent handling it would be flagged as handling an invented
    event."""
    assert "context_usage" in APP_FACING_EVENTS


def _run(usage: dict, model: str) -> list:
    """Drive the loop for one turn with a scripted assistant reply, and collect the events.

    The stream contract is `{"type":"done", "message": AssistantMessage}` (litellm.py) — the fully
    assembled turn, carrying the usage the provider reported and the model that served it.
    """
    from agent_runtime.domain.messages import AssistantMessage, TextContent
    from agent_runtime.infrastructure.engine.native import run_agent_loop

    events = []

    async def on_event(ev):
        events.append(ev)

    async def stream_fn(**_):
        yield {"type": "text_delta", "delta": "done"}
        yield {
            "type": "done",
            "message": AssistantMessage(
                content=[TextContent(text="done")], usage=usage, model=model
            ),
        }

    asyncio.run(
        run_agent_loop(
            messages=[],
            system_prompt="",
            tools=[],
            stream_fn=stream_fn,
            model=model,
            on_event=on_event,
            abort=asyncio.Event(),
        )
    )
    return events


def _usage_events(events):
    return [e for e in events if e.type == "context_usage"]


def test_it_reports_used_limit_and_percent():
    """The three numbers a meter needs, precomputed server-side so three windows cannot each
    round the percentage their own way."""
    found = _usage_events(_run({"input": 32_000, "output": 50}, "gpt-4o"))

    assert len(found) == 1
    payload = found[0].payload
    assert payload["used"] == 32_000
    assert payload["limit"] == 128_000
    assert payload["pct"] == 0.25
    assert payload["model"] == "gpt-4o"


def test_the_cached_subset_rides_along():
    """Not a second meter — it is why a large context can still be cheap. Without it, a user
    reading '180k used' cannot tell an expensive turn from a mostly-cached one."""
    found = _usage_events(_run({"input": 32_000, "output": 50, "cached": 30_000}, "gpt-4o"))
    assert found[0].payload["cached"] == 30_000


@pytest.mark.parametrize(
    "usage,model",
    [
        ({"input": 0, "output": 5}, "gpt-4o"),  # nothing billed yet -> nothing to report
        ({"input": 1000, "output": 5}, "unknown-model-xyz"),  # no denominator -> no meter
    ],
)
def test_it_stays_silent_when_a_half_is_missing(usage, model):
    """Both halves or nothing. Reporting a used count against an unknown limit would leave the
    client to invent the denominator, which is exactly the guess this avoids."""
    assert _usage_events(_run(usage, model)) == []

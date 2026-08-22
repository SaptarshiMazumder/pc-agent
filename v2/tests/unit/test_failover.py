"""S11 — model failover: retry the next model on a CLEAN primary error (no output yet);
pass the error through once output has streamed; no fallbacks => unwrapped."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agent_runtime.infrastructure.llm.failover import make_failover_stream


def _inner(behaviors):
    """behaviors: {model: 'ok' | 'error_clean' | 'error_after_output'}."""

    async def inner(*, model, system_prompt, messages, tools, abort):
        b = behaviors[model]
        if b == "error_clean":
            yield {"type": "done", "message": SimpleNamespace(stop_reason="error")}
        elif b == "error_after_output":
            yield {"type": "text_delta", "delta": "partial"}
            yield {"type": "done", "message": SimpleNamespace(stop_reason="error")}
        else:  # ok
            yield {"type": "text_delta", "delta": f"hi from {model}"}
            yield {"type": "done", "message": SimpleNamespace(stop_reason="stop")}

    return inner


async def _collect(stream, model):
    return [
        e
        async for e in stream(
            model=model, system_prompt="", messages=[], tools=[], abort=asyncio.Event()
        )
    ]


@pytest.mark.asyncio
async def test_failover_to_next_model_on_clean_error():
    stream = make_failover_stream(_inner({"A": "error_clean", "B": "ok"}), ["B"])
    evs = await _collect(stream, "A")
    assert any(e.get("delta") == "hi from B" for e in evs)  # B served the turn
    assert evs[-1]["message"].stop_reason == "stop"  # success, not error
    assert not any(
        getattr(e.get("message"), "stop_reason", None) == "error" for e in evs
    )  # A's error suppressed


@pytest.mark.asyncio
async def test_no_failover_after_output_started():
    stream = make_failover_stream(_inner({"A": "error_after_output", "B": "ok"}), ["B"])
    evs = await _collect(stream, "A")
    assert any(e.get("delta") == "partial" for e in evs)  # A's partial output kept
    assert evs[-1]["message"].stop_reason == "error"  # A's error passed through (no retry)


@pytest.mark.asyncio
async def test_all_models_fail_yields_last_error():
    stream = make_failover_stream(_inner({"A": "error_clean", "B": "error_clean"}), ["B"])
    evs = await _collect(stream, "A")
    assert evs[-1]["message"].stop_reason == "error"  # exhausted -> final error surfaces


def test_no_fallbacks_returns_inner_unchanged():
    inner = _inner({"A": "ok"})
    assert make_failover_stream(inner, []) is inner  # zero overhead when off


# ── per-account chains: the list is resolved PER TURN, not captured at boot ───────────────────
# On a hosted daemon the fallback chain belongs to the ACCOUNT, and the wrapper is installed once
# for the whole process — so it has to ask on every turn rather than close over a list.
@pytest.mark.asyncio
async def test_a_callable_chain_is_resolved_per_turn():
    chain: list[str] = []
    stream = make_failover_stream(_inner({"A": "error_clean", "B": "ok"}), lambda: list(chain))

    # first turn: this caller has no fallbacks -> the error is theirs to see
    evs = await _collect(stream, "A")
    assert [e["type"] for e in evs] == ["done"]
    assert not any(e["type"] == "fallback" for e in evs)

    # second turn: a caller who HAS configured one -> it takes over
    chain.append("B")
    evs = await _collect(stream, "A")
    assert any(e["type"] == "fallback" and e["to"] == "B" for e in evs)
    assert any(e.get("delta") == "hi from B" for e in evs)


@pytest.mark.asyncio
async def test_a_callable_that_raises_is_treated_as_no_fallbacks():
    """A fallback lookup reads a file. It must never be the reason a turn dies."""

    def boom():
        raise RuntimeError("overlay unreadable")

    stream = make_failover_stream(_inner({"A": "ok"}), boom)
    evs = await _collect(stream, "A")
    assert any(e.get("delta") == "hi from A" for e in evs)


@pytest.mark.asyncio
async def test_a_static_empty_chain_still_returns_the_stream_unwrapped():
    inner = _inner({"A": "ok"})
    assert make_failover_stream(inner, []) is inner
    assert make_failover_stream(inner, None) is inner

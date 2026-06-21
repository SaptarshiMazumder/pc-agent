"""S11 — model failover: retry the next model on a CLEAN primary error (no output yet);
pass the error through once output has streamed; no fallbacks => unwrapped."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agentd.infrastructure.llm.failover import make_failover_stream


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
    return [e async for e in stream(model=model, system_prompt="", messages=[],
                                    tools=[], abort=asyncio.Event())]


@pytest.mark.asyncio
async def test_failover_to_next_model_on_clean_error():
    stream = make_failover_stream(_inner({"A": "error_clean", "B": "ok"}), ["B"])
    evs = await _collect(stream, "A")
    assert any(e.get("delta") == "hi from B" for e in evs)            # B served the turn
    assert evs[-1]["message"].stop_reason == "stop"                   # success, not error
    assert not any(getattr(e.get("message"), "stop_reason", None) == "error" for e in evs)  # A's error suppressed


@pytest.mark.asyncio
async def test_no_failover_after_output_started():
    stream = make_failover_stream(_inner({"A": "error_after_output", "B": "ok"}), ["B"])
    evs = await _collect(stream, "A")
    assert any(e.get("delta") == "partial" for e in evs)             # A's partial output kept
    assert evs[-1]["message"].stop_reason == "error"                # A's error passed through (no retry)


@pytest.mark.asyncio
async def test_all_models_fail_yields_last_error():
    stream = make_failover_stream(_inner({"A": "error_clean", "B": "error_clean"}), ["B"])
    evs = await _collect(stream, "A")
    assert evs[-1]["message"].stop_reason == "error"                # exhausted -> final error surfaces


def test_no_fallbacks_returns_inner_unchanged():
    inner = _inner({"A": "ok"})
    assert make_failover_stream(inner, []) is inner                 # zero overhead when off

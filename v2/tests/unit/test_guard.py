import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_runtime.infrastructure.tools import Tool, ToolResult
from agent_runtime.infrastructure.tools.guard import (
    GuardedTool,
    ToolPolicy,
    is_transient,
    resolve_policy,
)


class FakeTool(Tool):
    name = "fake"
    description = "fake"
    label = "Fake"
    concurrency = "parallel"

    def __init__(self, *, sleep=0.0, exc=None, exc_times=0):
        self.calls = 0
        self.cancelled = False
        self.finally_ran = False
        self._sleep = sleep
        self._exc = exc
        self._exc_times = exc_times

    async def execute(self, tool_call_id, params, abort, on_update=None):
        self.calls += 1
        try:
            if self._exc and self.calls <= self._exc_times:
                raise self._exc
            if self._sleep:
                await asyncio.sleep(self._sleep)
            return ToolResult.text("ok")
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        finally:
            self.finally_ran = True


def _pol(
    timeout=None,
    max_retries=0,
    retryable=False,
    retry_on_timeout=False,
    loop_max_repeats=0,
    loop_warn_after_errors=0,
):
    return ToolPolicy(
        timeout,
        max_retries,
        retryable,
        retry_on_timeout,
        0.01,
        0.05,
        loop_max_repeats,
        loop_warn_after_errors,
    )


async def _run(tool, on_update=None, abort=None):
    return await tool.execute("c1", {}, abort or asyncio.Event(), on_update)


# ---- timeout ----------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_returns_error_and_cancels_inner():
    inner = FakeTool(sleep=5)
    res = await _run(GuardedTool(inner, _pol(timeout=0.05)))
    assert res.is_error and "timed out" in res.content[0].text
    assert inner.cancelled and inner.finally_ran  # inner cancelled, cleanup ran


@pytest.mark.asyncio
async def test_timeout_does_not_set_abort():
    abort = asyncio.Event()
    await _run(GuardedTool(FakeTool(sleep=5), _pol(timeout=0.05)), abort=abort)
    assert not abort.is_set()


@pytest.mark.asyncio
async def test_timeout_isolates_siblings():
    abort = asyncio.Event()  # shared, like the engine's gather batch
    slow = GuardedTool(FakeTool(sleep=5), _pol(timeout=0.05))
    fast = GuardedTool(FakeTool(sleep=0.0), _pol(timeout=2.0))
    r = await asyncio.gather(slow.execute("1", {}, abort), fast.execute("2", {}, abort))
    assert r[0].is_error and "timed out" in r[0].content[0].text
    assert not r[1].is_error and r[1].content[0].text == "ok"
    assert not abort.is_set()


@pytest.mark.asyncio
async def test_no_wrapper_when_timeout_none():
    inner = FakeTool(sleep=0.0)
    res = await _run(GuardedTool(inner, _pol(timeout=None)))
    assert not res.is_error and res.content[0].text == "ok"


# ---- retry ------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_succeeds_after_transient():
    inner = FakeTool(exc=ConnectionError("blip"), exc_times=1)
    res = await _run(GuardedTool(inner, _pol(retryable=True, max_retries=2)))
    assert not res.is_error and inner.calls == 2


@pytest.mark.asyncio
async def test_retry_exhausts_to_error():
    inner = FakeTool(exc=ConnectionError("down"), exc_times=99)
    res = await _run(GuardedTool(inner, _pol(retryable=True, max_retries=2)))
    assert res.is_error and inner.calls == 3  # 1 + 2 retries


@pytest.mark.asyncio
async def test_non_transient_not_retried():
    inner = FakeTool(exc=ValueError("bad"), exc_times=99)
    res = await _run(GuardedTool(inner, _pol(retryable=True, max_retries=2)))
    assert res.is_error and inner.calls == 1


@pytest.mark.asyncio
async def test_not_retried_when_not_retryable():
    inner = FakeTool(exc=ConnectionError("x"), exc_times=99)
    res = await _run(GuardedTool(inner, _pol(retryable=False, max_retries=2)))
    assert res.is_error and inner.calls == 1


@pytest.mark.asyncio
async def test_timeout_not_retried_by_default():
    inner = FakeTool(sleep=5)
    res = await _run(GuardedTool(inner, _pol(timeout=0.05, retryable=True, max_retries=2)))
    assert res.is_error and "timed out" in res.content[0].text and inner.calls == 1


@pytest.mark.asyncio
async def test_on_update_retry_notice():
    notices = []
    inner = FakeTool(exc=ConnectionError("x"), exc_times=1)
    res = await _run(
        GuardedTool(inner, _pol(retryable=True, max_retries=2)),
        on_update=lambda u: notices.append(u),
    )
    assert not res.is_error and notices  # a retry notice was emitted


# ---- loop detection ---------------------------------------------------


@pytest.mark.asyncio
async def test_loop_blocks_identical_repeats():
    inner = FakeTool()
    g = GuardedTool(inner, _pol(loop_max_repeats=3))
    abort = asyncio.Event()
    # 3 identical calls allowed, 4th blocked without running the inner tool
    for _ in range(3):
        r = await g.execute("c", {"command": "x"}, abort)
        assert not r.is_error
    blocked = await g.execute("c", {"command": "x"}, abort)
    assert blocked.is_error and "loop guard" in blocked.content[0].text
    assert inner.calls == 3  # inner never ran on the blocked call


@pytest.mark.asyncio
async def test_loop_resets_on_different_args():
    inner = FakeTool()
    g = GuardedTool(inner, _pol(loop_max_repeats=2))
    abort = asyncio.Event()
    await g.execute("c", {"command": "a"}, abort)
    await g.execute("c", {"command": "a"}, abort)
    # different args -> counter resets, not blocked
    r = await g.execute("c", {"command": "b"}, abort)
    assert not r.is_error and inner.calls == 3


@pytest.mark.asyncio
async def test_loop_nudge_after_consecutive_errors():
    inner = FakeTool(exc=ValueError("bad"), exc_times=99)  # always errors (non-transient)
    g = GuardedTool(inner, _pol(loop_warn_after_errors=2))
    abort = asyncio.Event()
    r1 = await g.execute("c", {"n": 1}, abort)
    assert "loop guard" not in r1.content[-1].text
    r2 = await g.execute("c", {"n": 2}, abort)  # 2nd consecutive error -> nudge appended
    assert any("loop guard" in b.text for b in r2.content)


# ---- delegation + helpers --------------------------------------------


def test_attribute_delegation():
    g = GuardedTool(FakeTool(), _pol())
    assert g.name == "fake" and g.label == "Fake" and g.concurrency == "parallel"
    assert g.description == "fake" and isinstance(g.parameters, dict)


def test_is_transient():
    assert is_transient(ConnectionError())
    assert is_transient(RuntimeError("HTTP 503 unavailable"))
    assert not is_transient(ValueError("bad path"))


# ---- resolve_policy precedence ---------------------------------------


class _ToolNoneDefault:
    name = "x"
    default_timeout_sec = None  # explicit opt-out must pass through (not global)
    default_retryable = True


class _ToolNoDefaults:
    name = "y"


def _cfg(overrides=None):
    return SimpleNamespace(
        tool_overrides=overrides or {}, tool_timeout_default=300.0, tool_retries_default=0
    )


def test_resolve_tool_default_none_passthrough():
    p = resolve_policy(_cfg(), _ToolNoneDefault())
    assert p.timeout_sec is None and p.retryable is True


def test_resolve_global_default_when_unset():
    p = resolve_policy(_cfg(), _ToolNoDefaults())
    assert p.timeout_sec == 300.0 and p.retryable is False


def test_resolve_override_wins():
    p = resolve_policy(_cfg({"x": {"timeout_sec": 7, "retryable": False}}), _ToolNoneDefault())
    assert p.timeout_sec == 7 and p.retryable is False

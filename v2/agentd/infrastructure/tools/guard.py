"""GuardedTool — cross-cutting reliability middleware wrapped around EVERY tool.

One decorator, wired once in the composition root, gives all tools (present and
future) the same guardrails: a per-tool wall-clock **timeout**, transient-error
**retry** with backoff, and **error normalization**. It IS a `Tool` (it delegates
name/description/parameters/label/concurrency to the inner tool), so the engine
sees no difference.

Per-tool effective values resolve: tool_overrides[name] > the tool's own declared
default (a `default_<field>` class attr) > the global default. A tool can declare
`default_timeout_sec = None` to opt OUT of the timeout wrapper (e.g. `exec`, which
self-limits) — that's why resolution uses a sentinel, not None-means-unset.

(Governance — Policy authorize / human Approval — is the next layer at this same
chokepoint; not built here.)
"""

from __future__ import annotations

import asyncio
import json
import socket
from collections import deque
from dataclasses import dataclass

from agentd.domain.messages import TextContent

from . import Tool, ToolResult

_UNSET = object()

# Transient (retryable) error heuristic — same shape as the computer driver's.
_TRANSIENT = (socket.gaierror, ConnectionError, TimeoutError)


def is_transient(exc: Exception) -> bool:
    if isinstance(exc, _TRANSIENT):
        return True
    s = (type(exc).__name__ + " " + str(exc)).lower()
    return any(k in s for k in (
        "getaddrinfo", "temporarily", "timeout", "timed out", "connection",
        "unavailable", "reset", "502", "503", "504", "429"))


@dataclass(frozen=True)
class ToolPolicy:
    timeout_sec: float | None       # None => no wait_for wrapper (tool self-limits)
    max_retries: int                # extra attempts after the first
    retryable: bool                 # may retry on transient errors at all
    retry_on_timeout: bool          # also retry when the wrapper times out
    base_backoff_sec: float
    max_backoff_sec: float
    loop_max_repeats: int = 0           # block after this many IDENTICAL consecutive calls (0 = off)
    loop_warn_after_errors: int = 0     # nudge after this many consecutive errors (0 = off)


def _resolve(overrides: dict, tool, field: str, global_default):
    """override key (incl. explicit null) > declared default_<field> (incl. None) > global."""
    if field in overrides:
        return overrides[field]
    declared = getattr(tool, f"default_{field}", _UNSET)
    if declared is not _UNSET:
        return declared
    return global_default


def resolve_policy(config, tool) -> ToolPolicy:
    overrides = (getattr(config, "tool_overrides", None) or {}).get(tool.name, {}) or {}
    return ToolPolicy(
        timeout_sec=_resolve(overrides, tool, "timeout_sec", getattr(config, "tool_timeout_default", 300.0)),
        max_retries=_resolve(overrides, tool, "max_retries", getattr(config, "tool_retries_default", 0)),
        retryable=_resolve(overrides, tool, "retryable", False),
        retry_on_timeout=_resolve(overrides, tool, "retry_on_timeout", False),
        base_backoff_sec=_resolve(overrides, tool, "base_backoff_sec", 0.5),
        max_backoff_sec=_resolve(overrides, tool, "max_backoff_sec", 8.0),
        loop_max_repeats=_resolve(overrides, tool, "loop_max_repeats",
                                  getattr(config, "tool_loop_max_repeats_default", 5)),
        loop_warn_after_errors=_resolve(overrides, tool, "loop_warn_after_errors",
                                        getattr(config, "tool_loop_warn_after_errors_default", 4)),
    )


class _TimeoutSignal(Exception):
    """Internal: the wrapper's wait_for fired (distinct from a tool raising TimeoutError)."""


class GuardedTool(Tool):
    def __init__(self, inner: Tool, policy: ToolPolicy):
        self._inner = inner
        self._policy = policy
        # loop-detection state (per tool, lives for the session)
        self._last_fp: str | None = None
        self._repeat_count = 0
        self._consec_errors = 0

    # --- delegate the contract attributes to the inner tool ----------------
    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def description(self) -> str:
        return self._inner.description

    @property
    def parameters(self) -> dict:
        return self._inner.parameters

    @property
    def label(self) -> str:
        return self._inner.label

    @property
    def concurrency(self) -> str:
        return self._inner.concurrency

    # --- loop detection (same chokepoint as timeout/retry) ----------------
    def _fingerprint(self, params) -> str:
        try:
            return json.dumps(params, sort_keys=True, default=str)
        except Exception:  # noqa: BLE001
            return repr(params)

    def _loop_block(self, params) -> ToolResult | None:
        """Block an IDENTICAL call repeated too many times in a row."""
        limit = self._policy.loop_max_repeats
        fp = self._fingerprint(params)
        if fp == self._last_fp:
            self._repeat_count += 1
        else:
            self._last_fp = fp
            self._repeat_count = 1
        if limit and self._repeat_count > limit:
            return ToolResult.text(
                f"loop guard: '{self.name}' was called with identical arguments "
                f"{self._repeat_count} times in a row. Stop repeating this exact call — "
                f"change the arguments, switch tools (e.g. use the browser for "
                f"login-walled/blocked content), or report the blocker to the user.",
                is_error=True,
            )
        return None

    def _loop_annotate(self, result: ToolResult) -> ToolResult:
        """Soft nudge after several consecutive errors from this tool."""
        warn = self._policy.loop_warn_after_errors
        self._consec_errors = self._consec_errors + 1 if result.is_error else 0
        if warn and self._consec_errors >= warn:
            result.content.append(TextContent(text=(
                f"\n\n[loop guard] '{self.name}' has failed {self._consec_errors} times "
                f"in a row. Stop retrying the same approach — try a different tool "
                f"(e.g. the browser for gated content) or report the blocker."
            )))
        return result

    # --- the guarded execution --------------------------------------------
    async def execute(self, tool_call_id, params, abort, on_update=None) -> ToolResult:
        blocked = self._loop_block(params)
        if blocked is not None:
            return blocked
        result = await self._guarded_run(tool_call_id, params, abort, on_update)
        return self._loop_annotate(result)

    async def _guarded_run(self, tool_call_id, params, abort, on_update=None) -> ToolResult:
        p = self._policy
        attempt = 0
        while True:
            try:
                return await self._run_once(tool_call_id, params, abort, on_update)
            except _TimeoutSignal:
                if p.retryable and p.retry_on_timeout and attempt < p.max_retries and not abort.is_set():
                    attempt += 1
                    self._notice(on_update, f"timed out, retrying {attempt}/{p.max_retries}")
                    await self._backoff(attempt, abort)
                    continue
                return ToolResult.text(
                    f"tool '{self.name}' timed out after {p.timeout_sec}s.", is_error=True
                )
            except asyncio.CancelledError:
                raise  # a real cancellation (e.g. run aborted) must propagate
            except Exception as e:  # noqa: BLE001
                if p.retryable and is_transient(e) and attempt < p.max_retries and not abort.is_set():
                    attempt += 1
                    self._notice(on_update, f"transient {type(e).__name__}, retrying {attempt}/{p.max_retries}")
                    await self._backoff(attempt, abort)
                    continue
                return ToolResult.text(
                    f"tool '{self.name}' failed: {type(e).__name__}: {e}", is_error=True
                )

    async def _run_once(self, tool_call_id, params, abort, on_update) -> ToolResult:
        if self._policy.timeout_sec:
            try:
                # wait_for cancels the INNER coroutine only on timeout (its try/finally
                # runs); it never touches the shared `abort`, so sibling parallel tools
                # and the whole run are unaffected.
                return await asyncio.wait_for(
                    self._inner.execute(tool_call_id, params, abort, on_update),
                    timeout=self._policy.timeout_sec,
                )
            except asyncio.TimeoutError:
                raise _TimeoutSignal from None
        return await self._inner.execute(tool_call_id, params, abort, on_update)

    async def _backoff(self, attempt: int, abort) -> None:
        delay = min(self._policy.base_backoff_sec * (2 ** (attempt - 1)), self._policy.max_backoff_sec)
        try:  # sleep, but wake early if the run is aborted
            await asyncio.wait_for(abort.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass

    def _notice(self, on_update, msg: str) -> None:
        if on_update:
            try:
                on_update(ToolResult.text(f"{self.name}: {msg}"))
            except Exception:
                pass

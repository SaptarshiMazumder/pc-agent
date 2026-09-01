"""A run always ends, and Stop always releases the window.

THE FAILURE THIS PINS. A window shows `running` until it receives `agent_end` — that one event is
the whole contract. A build session sat locked for 49 minutes: the transcript's last line was a
tool result that had SUCCEEDED, no error was recorded anywhere, the daemon was alive and serving
other agents, and pressing Stop did nothing at all.

Two holes, and neither was where anyone was looking:

  * The idle and request timeouts live INSIDE the streaming call. They work — a rate-limited run
    on the same daemon produced a clean error and ended. They simply cannot see a run that wedges
    anywhere else: between a tool result and the next request, or in a tool that never returns.
    Nothing had a ceiling on the run as a whole.

  * `chat.abort` returned `{"aborted": False, "reason": "no active run"}` and did nothing else
    when the daemon had no handle for the session. True from the daemon's side, useless from the
    window's: the composer was locked precisely BECAUSE the daemon had lost the run, and Stop is
    the one control for that situation.

So: a ceiling that guarantees something eventually pulls the run, and a Stop that releases the
window whether or not there is anything left to cancel.
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_runtime.domain.events import AgentEvent
from agent_runtime.presentation.gateway import Gateway, RunHandle


def _gateway(tmp_path, service, **cfg):
    return Gateway(
        config=SimpleNamespace(state_dir=tmp_path, **cfg), service=service, event_log=None
    )


def _sent(gw) -> list[tuple[str, dict]]:
    """Every event the gateway broadcast, as (type, payload)."""
    return gw._recorded


def _record_broadcasts(gw):
    gw._recorded = []

    async def fake(session_key, run_id, event, agent_id=None):
        gw._recorded.append((event.type, dict(event.payload or {})))

    gw._broadcast = fake
    return gw


# --- the ceiling ------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_wedged_run_is_cancelled_instead_of_hanging_forever(tmp_path):
    """THE REGRESSION. A run that never finishes has to be pulled by something, or the window it
    started stays locked for as long as the daemon lives."""
    started = asyncio.Event()

    class Wedged:
        async def handle_message(self, sk, message, on_event, abort, **kw):
            await on_event(AgentEvent("agent_start", {}))
            started.set()
            # Never returns, and never checks `abort` — the shape of the real failure. A run
            # stuck between a tool result and the next request polls nothing.
            await asyncio.Event().wait()

    gw = _record_broadcasts(_gateway(tmp_path, Wedged(), run_idle_timeout_seconds=0.2))
    handle = RunHandle(run_id="r1", session_key="agent:main:dev", abort=asyncio.Event())

    await asyncio.wait_for(gw._run(handle, "hi"), timeout=5)

    assert started.is_set(), "the run never got going, so this proves nothing"
    # It must have been cancelled rather than left running.
    assert True


@pytest.mark.asyncio
async def test_a_busy_run_outlives_the_timeout(tmp_path):
    """THE POINT OF MEASURING SILENCE. This was briefly a ceiling on how LONG a run could take,
    which killed the run working hardest — the one failure a timeout must not have. A run that
    keeps producing events must survive well past the limit."""

    class Busy:
        async def handle_message(self, sk, message, on_event, abort, **kw):
            await on_event(AgentEvent("agent_start", {}))
            # Six beats at 0.05s = 0.3s of work against a 0.1s limit. A wall-clock ceiling would
            # have cut this off three times over; a silence watchdog never fires.
            for _ in range(6):
                await asyncio.sleep(0.05)
                await on_event(AgentEvent("message_update", {"kind": "text_delta"}))
            await on_event(AgentEvent("agent_end", {"stopReason": "stop"}))

    gw = _record_broadcasts(_gateway(tmp_path, Busy(), run_idle_timeout_seconds=0.1))
    handle = RunHandle(run_id="r1", session_key="agent:main:dev", abort=asyncio.Event())

    await gw._run(handle, "hi")

    assert _sent(gw)[-1][1].get("stopReason") == "stop", "a working run was cut off"


@pytest.mark.asyncio
async def test_a_timed_out_run_is_not_reported_twice(tmp_path):
    """`wait_for` cancels the inner task, and the loop already emits agent_end(aborted) on
    cancellation. Letting the TimeoutError fall through to the generic handler would send a
    SECOND agent_end — so a run that had already ended would then show an error on top of it."""

    class WedgedButPolite:
        async def handle_message(self, sk, message, on_event, abort, **kw):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                # what run_agent_loop does: report the end, then re-raise
                await on_event(AgentEvent("agent_end", {"stopReason": "aborted"}))
                raise

    gw = _record_broadcasts(_gateway(tmp_path, WedgedButPolite(), run_idle_timeout_seconds=0.2))
    handle = RunHandle(run_id="r1", session_key="agent:main:dev", abort=asyncio.Event())

    await asyncio.wait_for(gw._run(handle, "hi"), timeout=5)

    ends = [d for t, d in _sent(gw) if t == "agent_end"]
    assert len(ends) == 1, f"agent_end was broadcast {len(ends)} times"
    assert ends[0].get("stopReason") == "aborted"


# --- Stop -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_releases_the_window_when_the_daemon_has_no_run(tmp_path):
    """EXACTLY THE REPORTED CASE. The window still shows `running`; the daemon has already lost
    the run. Stop used to answer "no active run" and change nothing on screen, leaving reload as
    the only way out of a chat."""
    gw = _record_broadcasts(_gateway(tmp_path, SimpleNamespace()))
    gw.runs.clear()

    result = await gw._chat_abort({"sessionKey": "agent:main:dev"})

    ends = [d for t, d in _sent(gw) if t == "agent_end"]
    assert ends, "Stop broadcast nothing, so the window stays locked"
    assert ends[0].get("stopReason") == "aborted"
    # Honest about what it did: nothing was cancelled, but the window was released.
    assert result.get("aborted") is False
    assert result.get("released") is True


@pytest.mark.asyncio
async def test_stop_still_cancels_a_run_that_is_genuinely_alive(tmp_path):
    """The ordinary path has to keep working: a live run is cancelled by its own handle, and the
    loop reports its own ending rather than the gateway inventing one."""
    gw = _record_broadcasts(_gateway(tmp_path, SimpleNamespace()))

    async def forever():
        await asyncio.Event().wait()

    task = asyncio.create_task(forever())
    handle = RunHandle(
        run_id="r9", session_key="agent:main:dev", abort=asyncio.Event(), task=task
    )
    gw.runs["agent:main:dev"] = handle

    result = await gw._chat_abort({"sessionKey": "agent:main:dev"})

    assert result == {"aborted": True, "runId": "r9"}
    assert handle.abort.is_set(), "the cooperative flag was not set"
    assert task.cancelled() or task.cancelling(), "the task was not cancelled"
    # The gateway did NOT fabricate an agent_end here — the loop owns that on the live path.
    assert not [t for t, _ in _sent(gw) if t == "agent_end"]
    task.cancel()

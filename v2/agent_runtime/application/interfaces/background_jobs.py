"""BackgroundJobs — the port through which a long tool call leaves the turn.

WHY A TOOL CALL MAY NOT HOLD THE CONVERSATION. A turn is `await tool.execute(...)`; while that
awaits, the model cannot hear the person. That was fine when a tool answered in seconds. It is
not fine for a tool that WAITS — `comfy_validate` holding until a 19 GB weight finishes
downloading held one chat for ten minutes with a spinner and nothing to say, and the only way
to be heard was Stop. A wait is a job, not a call.

THE RULE IS THE ENGINE'S, NOT THE MODEL'S. A tool that DECLARED it may wait long
(`default_timeout_sec` at or above the engine's threshold) and is still running after a grace
period is handed here; the model receives a provisional answer saying so, the turn goes on or
ends, and the result comes back into the conversation as a message when it lands. Every model
gets the same behaviour, because no model is asked to do anything.

THIS MODULE IS THE CONTRACT AND THE WORDS. The wording the model reads — "continues as job
j1", "already running", "finished after 23m" — lives on the base class so every engine and
every transport says the same thing; only the lifecycle (adopt, watch, cancel, notify) is
abstract, and `application/services/background_job_registry.py` is the one implementation.
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from agent_runtime.application.interfaces.tool import ToolResult

#: How a runtime-written message about a background job begins. The engine's incomplete-turn
#: guard reads it (is_injected_prompt) to tell the runtime's voice from the person's, and the
#: model is told to expect it.
BACKGROUND_JOB_PREFIX = "[background job]"


def clock(seconds: float) -> str:
    """`4m12s`, `37s` — the one spelling of a duration these messages use."""
    s = max(0, int(seconds))
    return f"{s // 60}m{s % 60:02d}s" if s >= 60 else f"{s}s"


@dataclass
class BackgroundJob:
    """One tool call that left its turn and is still running.

    `task` is the tool's own coroutine, exactly as the engine started it — the guard's timeout
    and retries are inside it, so a job is bounded by the same policy the inline call was.
    `last_progress` is the newest line the tool reported (`on_update`), which is what the
    window shows and what the provisional answer quotes."""

    id: str
    tool: str
    args: dict[str, Any]
    tool_call_id: str
    task: asyncio.Task
    #: monotonic seconds when the call started (elapsed is measured from the CALL, not the
    #: detach — "still working after 20s" counts from when the model asked).
    started_mono: float
    #: epoch seconds of the same moment, for clients and the sidecar.
    started_at: float
    last_progress: str = ""
    state: str = "running"  # running | done | cancelled
    result: ToolResult | None = None
    ended_mono: float | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    def elapsed_s(self) -> float:
        end = self.ended_mono if self.ended_mono is not None else time.monotonic()
        return max(0.0, end - self.started_mono)


class BackgroundJobs(ABC):
    """One session's background jobs, as the engine sees them.

    The engine calls `pending_for`/`room` BEFORE running a long-declared tool (the brake
    against a model calling the same waiting tool again — and again — which is the polling
    loop this whole design exists to end), `adopt` when the call is still going at the grace
    period, and `progress` for every line the tool reports afterwards. Delivery of the
    result into the conversation is the transport's concern, not the engine's, and not here.
    """

    @abstractmethod
    def jobs(self) -> list[BackgroundJob]:
        """The jobs still running, oldest first."""

    @abstractmethod
    def pending_for(self, tool: str, args: dict[str, Any]) -> BackgroundJob | None:
        """A running job for this exact call (same tool, same arguments), if any."""

    @abstractmethod
    def room(self) -> bool:
        """May one more call leave the turn?"""

    @abstractmethod
    def adopt(
        self,
        *,
        tool: str,
        args: dict[str, Any],
        tool_call_id: str,
        task: asyncio.Task,
        started_mono: float,
        last_progress: str = "",
    ) -> BackgroundJob:
        """Take a still-running call off the turn. Returns its job."""

    @abstractmethod
    def progress(self, job: BackgroundJob, text: str) -> None:
        """The tool reported a line. Synchronous: it is called from the tool's own callback."""

    # ---------------------------------------------------------------- the words

    def detached_text(self, job: BackgroundJob) -> str:
        """The provisional answer the model gets in place of the result."""
        seen = f" Last progress: {job.last_progress}" if job.last_progress else ""
        return (
            f"{job.tool} is still working after {clock(job.elapsed_s())} and continues in the "
            f"background as job {job.id}.{seen} Its result will arrive in this conversation as a "
            f"message beginning '{BACKGROUND_JOB_PREFIX}' when it is done. Do not call "
            f"{job.tool} again for this. If nothing else can be done meanwhile, end your turn now "
            f"and tell the user in one line what is being waited on."
        )

    def already_running_text(self, job: BackgroundJob) -> str:
        """The brake: the same call is already a job."""
        last = job.last_progress or "none yet"
        return (
            f"{job.tool} with these arguments is already running in the background as job "
            f"{job.id} ({clock(job.elapsed_s())} so far; last progress: {last}). Its result "
            f"will arrive as a message beginning '{BACKGROUND_JOB_PREFIX}'. Do not call it "
            f"again; end your turn if nothing else can be done meanwhile."
        )

    def no_room_text(self, tool: str) -> str:
        """The cap: enough calls are waiting already."""
        waiting = ", ".join(f"{j.tool} ({j.id}, {clock(j.elapsed_s())})" for j in self.jobs())
        return (
            f"{tool} was not started: {len(self.jobs())} calls are already waiting in the "
            f"background ({waiting}) and each result will arrive as a message beginning "
            f"'{BACKGROUND_JOB_PREFIX}'. Wait for them; do not call again. End your turn if "
            f"nothing else can be done meanwhile."
        )

    def delivery_text(self, job: BackgroundJob) -> str:
        """The message that carries a finished job's result back into the conversation."""
        result = job.result
        body = "".join(getattr(b, "text", "") for b in (result.content if result else []))
        verb = "failed" if result is not None and result.is_error else "finished"
        head = f"{BACKGROUND_JOB_PREFIX} {job.tool} (job {job.id}) {verb} after {clock(job.elapsed_s())}."
        return f"{head}\n{body}" if body.strip() else head

    def lost_text(self, tool: str, job_id: str) -> str:
        """What a job that died with the daemon says, once, when the conversation resumes."""
        return (
            f"{BACKGROUND_JOB_PREFIX} {tool} (job {job_id}) was lost when the daemon restarted "
            f"before it finished. Call {tool} again if its result is still needed."
        )

    def cancelled_text(self, job: BackgroundJob) -> str:
        """What a job the person stopped says in the transcript — so the model does not wait
        for a result that is never coming."""
        return (
            f"{BACKGROUND_JOB_PREFIX} {job.tool} (job {job.id}) was cancelled by the user after "
            f"{clock(job.elapsed_s())}. Call {job.tool} again only if asked to."
        )

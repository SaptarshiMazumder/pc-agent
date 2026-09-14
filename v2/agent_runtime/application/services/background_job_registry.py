"""BackgroundJobRegistry — one session's background jobs, owned here and nowhere else.

The engine hands it a still-running tool call (see interfaces/background_jobs.py); it keeps
the task, watches it to its end, records every progress line, and tells ONE listener what
changed — `start`, `progress`, `done`, `cancelled`. The listener is the transport's: the
gateway broadcasts the change to the session's windows and, on `done`, puts the result back
into the conversation. Nothing here knows a socket, a file, or a window.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from agent_runtime.application.interfaces.background_jobs import BackgroundJob, BackgroundJobs
from agent_runtime.application.interfaces.tool import ToolResult

log = logging.getLogger("agentd")

#: How many calls one session may have waiting in the background at once. Beyond it a long
#: call is refused with the jobs named (BackgroundJobs.no_room_text) rather than run inline —
#: the inline run is the held turn this exists to end, and three waits on one instance is
#: already two more than the workflow needs.
MAX_JOBS = 3

OnJobChange = Callable[[BackgroundJob, str], Awaitable[None]]


class BackgroundJobRegistry(BackgroundJobs):
    def __init__(self, session_key: str, on_change: OnJobChange):
        self.session_key = session_key
        self._on_change = on_change
        self._jobs: dict[str, BackgroundJob] = {}
        self._watchers: dict[str, asyncio.Task] = {}
        self._seq = 0

    # ---------------------------------------------------------------- the port

    def jobs(self) -> list[BackgroundJob]:
        return [j for j in self._jobs.values() if j.state == "running"]

    def pending_for(self, tool: str, args: dict[str, Any]) -> BackgroundJob | None:
        for job in self.jobs():
            if job.tool == tool and job.args == args:
                return job
        return None

    def room(self) -> bool:
        return len(self.jobs()) < MAX_JOBS

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
        self._seq += 1
        job = BackgroundJob(
            id=f"j{self._seq}",
            tool=tool,
            args=dict(args),
            tool_call_id=tool_call_id,
            task=task,
            started_mono=started_mono,
            started_at=time.time() - max(0.0, time.monotonic() - started_mono),
            last_progress=last_progress,
        )
        self._jobs[job.id] = job
        # The watcher is created HERE, inside the run's task, so it inherits the run's context —
        # its account, its RunContext, its trace ids — and the listener it calls on `done` acts
        # as that run did.
        self._watchers[job.id] = asyncio.create_task(
            self._watch(job), name=f"background-job-{self.session_key}-{job.id}"
        )
        self._notify_soon(job, "start")
        return job

    def progress(self, job: BackgroundJob, text: str) -> None:
        if not text or job.state != "running":
            return
        job.last_progress = text
        self._notify_soon(job, "progress")

    # ---------------------------------------------------------------- control

    def cancel(self, job_id: str) -> bool:
        """Stop one job. The watcher reports `cancelled` once the task has let go."""
        job = self._jobs.get(job_id)
        if job is None or job.state != "running":
            return False
        job.task.cancel()
        return True

    def cancel_all(self) -> int:
        """Stop every running job — the Stop button's meaning. Returns how many were told to."""
        return sum(1 for job in self.jobs() if self.cancel(job.id))

    def snapshot(self) -> list[dict[str, Any]]:
        """The running jobs as a client sees them (chat.status, the sidecar)."""
        return [
            {
                "jobId": j.id,
                "toolName": j.tool,
                "toolCallId": j.tool_call_id,
                "startedAt": j.started_at,
                "elapsed": round(j.elapsed_s()),
                "text": j.last_progress,
            }
            for j in self.jobs()
        ]

    # ---------------------------------------------------------------- inside

    async def _watch(self, job: BackgroundJob) -> None:
        try:
            result = await job.task
        except asyncio.CancelledError:
            if not job.task.cancelled() and not job.task.done():
                # The WATCHER was cancelled (daemon shutdown), not the job: the job goes with it.
                job.task.cancel()
            job.state = "cancelled"
            job.ended_mono = time.monotonic()
            await self._notify(job, "cancelled")
            return
        except Exception as e:  # noqa: BLE001 — a tool's crash is its result, not the daemon's
            result = ToolResult.text(
                f"{job.tool} failed in the background: {type(e).__name__}: {e}", is_error=True
            )
        job.result = result
        job.state = "done"
        job.ended_mono = time.monotonic()
        await self._notify(job, "done")

    def _notify_soon(self, job: BackgroundJob, kind: str) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        asyncio.create_task(self._notify(job, kind))

    async def _notify(self, job: BackgroundJob, kind: str) -> None:
        try:
            await self._on_change(job, kind)
        except Exception:  # noqa: BLE001 — a listener's failure must not lose the job's outcome
            log.exception("background job %s/%s: listener failed on %s", self.session_key, job.id, kind)

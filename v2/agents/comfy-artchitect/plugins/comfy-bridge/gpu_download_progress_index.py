"""One file on the GPU with every download's progress — what the daemon reads in ONE request.

WHY ONE FILE. Each download writes its own status file, and comfy_install read them one request
per file every five seconds. Six files at once used up the sandbox's request budget for the whole
tool run in three and a half minutes; from then on every read was refused, the install reported
"tracking unavailable" and gave up while the downloads carried on fine. This index holds every
job's latest status, so one read covers them all however many there are.

NOT THE REAPER'S FILE. `activity.json` (GpuDownloadActivity) is the idle reaper's keepalive and
keeps only jobs still running; this one keeps finished and failed jobs too, for a while, because
the window shows them. Separate files, separate locks: neither can break the other.

Fixed, stdlib-only, shipped to the GPU with the worker.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

#: A finished job stays listed this long — long enough to be seen as done.
KEEP_FINISHED_S = 6 * 3600
FILE = "progress.json"


class GpuDownloadProgressIndex:
    def __init__(self, directory: Path, *, lock, now=time.time):
        self.directory = directory
        self.lock = lock
        self.now = now

    def update(self, job_id: str, record: dict) -> None:
        """Replace one job's entry. Other jobs' entries are kept — another worker process may be
        writing its own at the same moment, under the same lock."""
        path = self.directory / FILE
        with (self.directory / "progress.lock").open("a") as lock_file:
            self.lock(lock_file)
            try:
                jobs = json.loads(path.read_text(encoding="utf-8")).get("jobs") if path.exists() else {}
            except ValueError:
                jobs = {}  # display only: a torn file is replaced, never trusted
            if not isinstance(jobs, dict):
                jobs = {}
            now = self.now()
            jobs = {k: v for k, v in jobs.items()
                    if isinstance(v, dict) and now - float(v.get("updated_at") or 0) < KEEP_FINISHED_S}
            jobs[job_id] = record
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"jobs": jobs}), encoding="utf-8")
            temporary.replace(path)

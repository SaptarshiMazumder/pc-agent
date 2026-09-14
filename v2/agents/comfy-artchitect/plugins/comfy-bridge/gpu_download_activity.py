"""Publish one atomic, credential-free activity index for the Vast idle reaper."""

import json
import time
from pathlib import Path


class GpuDownloadActivity:
    def __init__(self, directory: Path, *, lock, now=time.time):
        self.directory = directory
        self.lock = lock
        self.now = now

    def update(self, job_id: str, state: str) -> None:
        path = self.directory / "activity.json"
        # A separate process may be downloading another destination. Never let one worker's
        # completion erase another's keepalive. The worker supplies its OS file-lock adapter.
        with (self.directory / "activity.lock").open("a") as lock_file:
            self.lock(lock_file)
            jobs = json.loads(path.read_text(encoding="utf-8"))["jobs"] if path.exists() else {}
            if not isinstance(jobs, dict):
                raise ValueError("Invalid GPU download activity index")
            if state in ("done", "failed"):
                jobs.pop(job_id, None)
            else:
                jobs[job_id] = {"state": state, "updated_at": self.now()}
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"jobs": jobs}), encoding="utf-8")
            temporary.replace(path)

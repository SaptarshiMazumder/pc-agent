"""Submit the fixed GPU worker through the rental's authenticated provisioning portal."""

from __future__ import annotations

import asyncio
import base64
import json
import shlex
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode

from model_download_request import ModelDownloadRequest


class GpuModelDownloadClient:
    def __init__(self, *, fetch, connection, current_connection, get, lease,
                 sleep=asyncio.sleep, clock=time.monotonic):
        self._fetch = fetch
        self._connection = connection
        self._current_connection = current_connection
        self._get = get
        self._lease = lease
        self._sleep = sleep
        self._clock = clock
        self._portal = None
        self._attempts = {}

    @staticmethod
    def command(request: ModelDownloadRequest, attempt_id: str = "") -> str:
        modules = {}
        for name in ("model_download_request", "model_download_redirect_policy", "gpu_download_activity", "gpu_model_download_worker"):
            modules[name] = Path(__file__).with_name(name + ".py").read_text(encoding="utf-8")
        bundle = base64.b64encode(json.dumps(modules).encode()).decode()
        payload = base64.b64encode(json.dumps(request.as_dict()).encode()).decode()
        # All source is ours; data is base64 JSON, never interpolated as shell/Python code.
        code = (
            "import base64,json,os,sys,types;from pathlib import Path;"
            f"sources=json.loads(base64.b64decode('{bundle}'));"
            "\nfor name,source in sources.items():\n"
            " module=types.ModuleType(name);sys.modules[name]=module;exec(compile(source,name,'exec'),module.__dict__)\n"
            "root=Path(os.environ.get('WORKSPACE','/workspace'))/'ComfyUI'\n"
            f"sys.modules['gpu_model_download_worker'].GpuModelDownloadWorker(root).run(json.loads(base64.b64decode('{payload}')), {attempt_id!r})\n"
            # Provisioner caches completed commands; retries must execute and acquire the
            # worker lock even when a previous attempt wrote a failure status and exited 0.
            f"# invocation {uuid.uuid4().hex}\n"
        )
        return "python3 -c " + shlex.quote(code)

    def start(self, request: ModelDownloadRequest) -> None:
        if self._portal is None:
            self._portal = self._connection()
        conn = self._portal
        active = self._current_connection() or {}
        if conn["url"].rstrip("/") != active.get("url", "").rstrip("/") or conn["auth"] != active.get("auth"):
            raise ValueError("GPU connection changed; call gpu_ensure before installing")
        previous = self.status(request)
        if previous and previous.get("state") == "done":
            return
        if previous and previous.get("state") in ("starting", "downloading", "verifying"):
            if time.time() - float(previous.get("updated_at", 0)) < 180:
                return  # worker still owns the download; no provisioning command duplication
        attempt_id = uuid.uuid4().hex
        self._attempts[request.job_id] = attempt_id
        manifest = {"version": 1, "post_commands": [self.command(request, attempt_id)],
                    "on_failure": {"action": "continue", "max_retries": 0}}
        response = self._fetch(
            conn["portal_url"].rstrip("/") + "/capabilities/provision", method="POST",
            headers={"Authorization": conn["auth"]},
            json={"inline_yaml": json.dumps(manifest)}, timeout_s=30,
        )
        if not response.ok:
            raise ValueError(f"GPU downloader could not start (portal HTTP {response.status}). "
                             "This GPU image must expose the authenticated provisioning API; "
                             "do not lower Manager security or claim the file installed.")
        if response.json().get("status") != "started":
            raise ValueError("GPU portal did not acknowledge the downloader; installation has not started")

    def status(self, request: ModelDownloadRequest) -> dict | None:
        query = urlencode({"filename": request.job_id + ".json", "type": "temp",
                           "subfolder": "agentd-model-downloads", "t": time.time_ns()})
        response = self._get("/api/view?" + query, timeout_s=15)
        if not response.ok:
            return None
        result = response.json()
        if result.get("source_id") != request.source_id:
            if result.get("state") == "failed":
                return None  # a corrected URL may retry a failed destination
            raise ValueError(f"{request.filename}: another source already owns this destination")
        expected = self._attempts.get(request.job_id)
        if expected and result.get("attempt_id") != expected:
            # A healthy concurrent worker won the lock: adopt it. Old failed status is
            # ignored until our new attempt publishes, not mistaken for a new failure.
            if (result.get("state") in ("starting", "downloading", "verifying", "done")
                    and time.time() - float(result.get("updated_at", 0)) < 180):
                self._attempts.pop(request.job_id, None)
            else:
                return None
        return result

    async def wait(self, requests: list[ModelDownloadRequest], abort, on_update=None) -> None:
        pending = {item.job_id: item for item in requests}
        started = self._clock()
        last_lease = started
        last_seen = {job_id: started for job_id in pending}
        reported = {}
        while pending:
            if abort is not None and abort.is_set():
                raise ValueError("Stopped waiting; GPU downloads may continue. No install success confirmed.")
            for job_id, request in list(pending.items()):
                status = self.status(request)
                if status is None:
                    if self._clock() - last_seen[job_id] > 120:
                        raise ValueError(f"{request.filename}: no GPU downloader status for 120s; installation unconfirmed")
                    continue
                last_seen[job_id] = self._clock()
                state = status.get("state")
                if state == "failed":
                    raise ValueError(f"{request.filename}: GPU download failed: {status.get('error', 'unknown error')}")
                if state != "done" and time.time() - float(status.get("updated_at", 0)) > 180:
                    raise ValueError(f"{request.filename}: GPU downloader stopped reporting progress; installation unconfirmed")
                progress = f"{request.filename}: GPU download {state}"
                if status.get("total"):
                    progress += f" ({int(status.get('received', 0)) * 100 // int(status['total'])}%)"
                if progress != reported.get(job_id) and on_update:
                    on_update(progress)
                reported[job_id] = progress
                if state == "done":
                    del pending[job_id]
            if self._clock() - last_lease >= 90:
                self._lease()
                last_lease = self._clock()
            if pending:
                await self._sleep(5)

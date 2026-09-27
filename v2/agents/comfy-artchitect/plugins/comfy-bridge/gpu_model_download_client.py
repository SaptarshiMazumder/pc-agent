"""Submit the fixed GPU worker through the rental's authenticated provisioning portal."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from urllib.parse import urlencode

from gpu_command_bundle import GpuCommandBundle
from model_download_request import ModelDownloadRequest
from gpu_model_download_failure import GpuModelDownloadFailure
from gpu_keepalive_unavailable import GpuKeepaliveUnavailable


class GpuModelDownloadClient:
    #: A downloader that runs on the machine — ModelInstallationService may route Hugging Face and
    #: Civitai files here (ManualModelDownloader is the one that cannot).
    available = True

    #: Everything the worker needs on the machine, shipped with it.
    MODULES = ("model_download_request", "model_download_redirect_policy", "gpu_download_activity",
               "model_download_resume_state", "model_storage_detector", "comfy_process_control",
               "model_storage_setup",
               "gpu_model_download_worker")

    def __init__(self, *, fetch, connection, current_connection, get, lease,
                 ready, use_volume=False, sleep=asyncio.sleep, clock=time.monotonic):
        self._fetch = fetch
        #: The person's own Vast machine: models go to its volume if it has one. Never on the
        #: platform's rented GPU, whose behaviour stays exactly as it was.
        self._use_volume = use_volume
        self._connection = connection
        self._current_connection = current_connection
        self._get = get
        self._lease = lease
        self._ready = ready
        self._sleep = sleep
        self._clock = clock
        self._portal = None
        self._attempts = {}

    @classmethod
    def command(cls, request: ModelDownloadRequest, attempt_id: str = "", use_volume: bool = False) -> str:
        # Every retry executes (GpuCommandBundle stamps each invocation) and takes the worker's
        # lock, even when a previous attempt wrote a failure status and exited 0.
        return GpuCommandBundle.command(
            cls.MODULES,
            "sys.modules['gpu_model_download_worker'].GpuModelDownloadWorker(root, use_volume=data['use_volume'])"
            ".run(data['request'], data['attempt_id'])",
            {"request": request.as_dict(), "attempt_id": attempt_id, "use_volume": bool(use_volume)},
        )

    def start(self, request: ModelDownloadRequest) -> None:
        if self._portal is None:
            self._portal = self._connection()
        conn = self._portal
        active = self._current_connection() or {}
        if conn["url"].rstrip("/") != active.get("url", "").rstrip("/") or conn["auth"] != active.get("auth"):
            raise ValueError("GPU connection changed; call gpu_ensure before installing")
        previous = self.status(request)
        if previous and previous.get("state") == "done" and self._ready(request):
            return
        if previous and previous.get("state") in ("starting", "downloading", "retrying", "verifying"):
            if time.time() - float(previous.get("updated_at", 0)) < 180:
                return  # worker still owns the download; no provisioning command duplication
        attempt_id = uuid.uuid4().hex
        self._attempts[request.job_id] = attempt_id
        manifest = {"version": 1, "post_commands": [self.command(request, attempt_id, self._use_volume)],
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
        try:
            result = response.json()
        except ValueError:
            return None
        if not isinstance(result, dict) or result.get("state") not in (
            "starting", "downloading", "retrying", "verifying", "done", "failed",
        ):
            return None
        if result.get("source_id") != request.source_id:
            if result.get("state") == "failed":
                return None  # a corrected URL may retry a failed destination
            raise ValueError(f"{request.filename}: another source already owns this destination")
        expected = self._attempts.get(request.job_id)
        if expected and result.get("attempt_id") != expected:
            # A healthy concurrent worker won the lock: adopt it. Old failed status is
            # ignored until our new attempt publishes, not mistaken for a new failure.
            if (result.get("state") in ("starting", "downloading", "retrying", "verifying", "done")
                    and time.time() - float(result.get("updated_at", 0)) < 180):
                self._attempts.pop(request.job_id, None)
            else:
                return None
        return result

    def active(self, request: ModelDownloadRequest) -> bool:
        previous = self.status(request)
        # Even stale progress may belong to a live transfer. Resume observation;
        # don't hand the same destination back to Manager on a tool retry.
        return bool(previous and previous.get("state") in ("starting", "downloading", "retrying", "verifying"))

    async def wait(self, requests: list[ModelDownloadRequest], abort, on_update=None) -> None:
        pending = {item.job_id: item for item in requests}
        started = self._clock()
        last_lease = started
        next_lease = started + 90
        lease_failures = 0
        last_seen = {job_id: started for job_id in pending}
        reported = {}
        last_report = ""
        while pending:
            if abort is not None and abort.is_set():
                raise ValueError("Stopped waiting; GPU downloads may continue. No install success confirmed.")
            for job_id, request in list(pending.items()):
                status = self.status(request)
                if status is None:
                    if self._clock() - last_seen[job_id] > 120:
                        if self._reconcile(request, on_update):
                            del pending[job_id]
                            continue
                        raise ValueError(f"{request.filename}: download tracking unavailable for 120s and "
                                         "ComfyUI has not confirmed the file; download may still be running")
                    continue
                last_seen[job_id] = self._clock()
                state = status.get("state")
                if state == "failed":
                    raise GpuModelDownloadFailure(request, status.get("error", "unknown error"),
                                                  status.get("http_status"))
                if state != "done" and time.time() - float(status.get("updated_at", 0)) > 180:
                    if self._reconcile(request, on_update):
                        del pending[job_id]
                        continue
                    raise ValueError(f"{request.filename}: GPU downloader stopped reporting progress; "
                                     "installation unconfirmed, download may still be running")
                progress = f"{request.filename}: GPU download {state}"
                if status.get("total"):
                    progress += f" ({int(status.get('received', 0)) * 100 // int(status['total'])}%)"
                if status.get("bytes_per_second") is not None:
                    progress += f" {status['bytes_per_second'] / (1024 * 1024):.1f} MiB/s"
                if status.get("attempt"):
                    progress += f" attempt {status['attempt']}"
                if status.get("resumed_from"):
                    progress += f" resumed at {status['resumed_from'] / (1024 * 1024):.1f} MiB"
                if state == "retrying":
                    progress += "; " + status.get("reason", "retrying transfer")
                reported[job_id] = progress
                if state == "done":
                    del pending[job_id]
            summary = "\n".join(reported.values())
            if summary != last_report and on_update:
                on_update(summary)
                last_report = summary
            if pending and self._clock() >= next_lease:
                try:
                    self._lease()
                except (GpuKeepaliveUnavailable, TimeoutError, ConnectionError) as error:
                    lease_failures += 1
                    if self._clock() - last_lease >= 300:
                        raise RuntimeError("GPU keepalive unconfirmed for 5 minutes; stopped tracking, "
                                           "not the GPU downloads. Reconnect before submitting more work; "
                                           "this does not prove lease expiry.") from error
                    next_lease = self._clock() + min(15 * 2 ** min(lease_failures - 1, 2), 60)
                    if on_update:
                        on_update(summary + "\nKeepalive temporarily unavailable; retrying without "
                                  "restarting downloads (lease expiry is not confirmed).")
                else:
                    if lease_failures and on_update:
                        on_update(summary + "\nKeepalive recovered; downloads were not restarted.")
                    lease_failures = 0
                    last_lease = self._clock()
                    next_lease = last_lease + 90
            if pending:
                await self._sleep(5)

    def _reconcile(self, request, on_update):
        if not self._ready(request):
            return False
        if on_update:
            on_update(f"{request.filename}: download tracking lost, but ComfyUI confirms the file is loadable")
        return True

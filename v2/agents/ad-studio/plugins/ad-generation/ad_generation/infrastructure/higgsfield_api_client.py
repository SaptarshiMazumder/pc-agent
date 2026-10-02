"""Higgsfield's developer API: upload an input, submit a generation, wait for its job, fetch the result.

    POST /media?type=image  ->  {id, upload_url}     a presigned S3 PUT (Content-Type and
    PUT   upload_url            the file, raw        If-None-Match are signed), then
    POST /media/{id}/confirm?type=image
    POST /videos/{job_type}/generations  {"params": {...}}  ->  {id, credits}
    GET  /jobs/{id}  ->  {status, result_url, ...}

THE CREDITS COME BACK WITH THE JOB ID — the charge is known the moment a job is accepted, so
every cost this adapter reports is exact, not estimated.
"""

from __future__ import annotations

import time
from pathlib import Path

from agent_runtime.infrastructure.net.outbound import fetch

from ad_generation.application.interfaces.provider_refused import ProviderRefused
from ad_generation.infrastructure.higgsfield_session import API, HiggsfieldSession

_PENDING = {"queued", "pending", "created", "in_progress", "processing", "running"}
_REFUSED_WORDS = ("moderation", "policy", "nsfw", "safety", "likeness", "not allowed", "rejected")
_NOT_A_REFUSAL = (401, 403, 408, 429)
_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


class HiggsfieldApiClient:
    def __init__(self, session: HiggsfieldSession, poll_s: float) -> None:
        self._session = session
        self._poll_s = poll_s

    def upload_image(self, path: str) -> str:
        """A workspace image -> its media id, for `start_image` and `image_references`."""
        mime = _MIME.get(Path(path).suffix.lower())
        if not mime:
            raise ValueError(f"{path}: not an image Higgsfield takes (png, jpg, webp)")
        created = self._json("POST", "/media", params={"type": "image"}, json={"content_type": mime})
        put = fetch(
            created["upload_url"],
            method="PUT",
            headers={"Content-Type": mime, "If-None-Match": "*"},
            file_path=path,
            raw_body=True,
            timeout_s=300,
        )
        if not put.ok:
            raise RuntimeError(f"Higgsfield upload of {path} failed: {put.error or f'HTTP {put.status}: {put.text[:300]}'}")
        self._json("POST", f"/media/{created['id']}/confirm", params={"type": "image"}, json={})
        return str(created["id"])

    def submit(self, kind: str, job_type: str, params: dict) -> tuple[str, float]:
        """-> (job id, credits charged)."""
        plural = {"image": "images", "video": "videos"}[kind]
        body = self._json("POST", f"/{plural}/{job_type}/generations", json={"params": params}, job_type=job_type)
        if not body.get("id"):
            raise RuntimeError(f"Higgsfield accepted {job_type} but returned no job id: {str(body)[:300]}")
        return str(body["id"]), float(body.get("credits") or 0.0)

    def wait(self, job_id: str, job_type: str, timeout_s: float) -> dict:
        deadline = time.monotonic() + timeout_s
        while True:
            job = self._json("GET", f"/jobs/{job_id}")
            status = str(job.get("status") or "")
            if status == "completed":
                if not job.get("result_url"):
                    raise RuntimeError(f"Higgsfield finished {job_type} with no result: {str(job)[:300]}")
                return job
            if status not in _PENDING:
                reason = str(job.get("error") or job.get("error_message") or job.get("failure_reason") or status)
                if any(w in reason.lower() for w in _REFUSED_WORDS) or status in ("nsfw", "rejected", "moderated"):
                    raise ProviderRefused("higgsfield", job_type, reason)
                raise RuntimeError(f"Higgsfield {job_type} job {job_id} {status}: {reason[:300]}")
            if time.monotonic() > deadline:
                raise TimeoutError(f"Higgsfield did not finish {job_type} within {int(timeout_s)} s (job {job_id})")
            time.sleep(self._poll_s)

    # ---- transport --------------------------------------------------------------------------

    def _json(self, method: str, path: str, params: dict | None = None, json=None, job_type: str = "") -> dict:
        res = fetch(API + path, method=method, headers=self._session.headers(), params=params, json=json, timeout_s=120)
        if not res.ok:
            status = int(res.status or 0)
            text = res.error or f"HTTP {status}: {res.text[:500]}"
            if job_type and 400 <= status < 500 and status not in _NOT_A_REFUSAL:
                # A 4xx about THIS input — moderation, a rejected file, a bad field.
                raise ProviderRefused("higgsfield", job_type, text)
            raise RuntimeError(f"Higgsfield {method} {path} failed: {text}")
        return res.json()

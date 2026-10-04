"""Higgsfield's developer API: upload an input, submit a generation, wait for its job, fetch the result.

    POST /media?type=image  ->  {id, upload_url,     a presigned S3 PUT, signed for the
                                 content_type}       content_type it names (a PNG) and
    PUT   upload_url            the file, raw        If-None-Match; then
    POST /media/{id}/confirm?type=image
    POST /videos/{job_type}/generations  {"params": {...}}  ->  {id, credits}
    GET  /jobs/{id}  ->  {status, result_url, ...}

THE CREDITS COME BACK WITH THE JOB ID — the charge is known the moment a job is accepted, so
every cost this adapter reports is exact, not estimated.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from PIL import Image

from agent_runtime.infrastructure.net.outbound import fetch

from ad_generation.application.interfaces.provider_refused import ProviderRefused
from ad_generation.infrastructure.higgsfield_session import API, HiggsfieldSession
from ad_generation.infrastructure.run_workspace import RunWorkspace

# Where a non-PNG input is converted before upload: the upload slot is signed for a PNG.
_PNG_CACHE = "campaigns/.upload"

_PENDING = {"queued", "pending", "created", "in_progress", "processing", "running"}
_REFUSED_WORDS = ("moderation", "policy", "nsfw", "safety", "likeness", "not allowed", "rejected")
_NOT_A_REFUSAL = (401, 403, 408, 429)
_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


class HiggsfieldApiClient:
    def __init__(self, session: HiggsfieldSession, workspace: RunWorkspace, poll_s: float) -> None:
        self._session = session
        self._ws = workspace
        self._poll_s = poll_s

    def upload_image(self, path: str) -> str:
        """A workspace image -> its media id, for `start_image` and `image_references`."""
        if not _MIME.get(Path(path).suffix.lower()):
            raise ValueError(f"{path}: not an image Higgsfield takes (png, jpg, webp)")
        created = self._json("POST", "/media", params={"type": "image"}, json={})
        signed = str(created.get("content_type") or "image/png")
        return self._put(created, "image", self._as_png(path) if signed == "image/png" else path, signed, path)

    def upload_video(self, path: str) -> str:
        """A workspace .mp4 -> its media id, for `video_references` (a clip to edit or extend)."""
        if Path(path).suffix.lower() != ".mp4":
            raise ValueError(f"{path}: not a clip Higgsfield takes (mp4)")
        created = self._json("POST", "/media", params={"type": "video"}, json={})
        return self._put(created, "video", path, str(created.get("content_type") or "video/mp4"), path)

    def _put(self, created: dict, kind: str, source: str, signed: str, original: str) -> str:
        """Fill the presigned slot (signed for `signed`), then confirm it."""
        put = fetch(
            created["upload_url"],
            method="PUT",
            headers={"Content-Type": signed, "If-None-Match": "*"},
            file_path=source,
            raw_body=True,
            timeout_s=300,
        )
        if not put.ok:
            raise RuntimeError(
                f"Higgsfield upload of {original} failed: {put.error or f'HTTP {put.status}: {put.text[:300]}'}"
            )
        self._json("POST", f"/media/{created['id']}/confirm", params={"type": kind}, json={})
        return str(created["id"])

    def quote(self, job_type: str, params: dict) -> float:
        """The credits a job with these settings will charge — asked before it is submitted. Only
        the settings ride (no prompt, no media): the price depends on those alone."""
        settings = {k: v for k, v in params.items() if not isinstance(v, (dict, list)) and k != "prompt"}
        return float(self._json("POST", f"/jobs/{job_type}/cost", json={"params": settings}).get("credits") or 0.0)

    def _as_png(self, path: str) -> str:
        """A PNG of the image — the file itself when it already is one, else a cached copy."""
        if Path(path).suffix.lower() == ".png":
            return path
        src = self._ws.path(path)
        rel = f"{_PNG_CACHE}/{hashlib.sha1(str(src).encode()).hexdigest()[:16]}.png"
        dest = self._ws.path(rel)
        if not dest.is_file() or dest.stat().st_mtime < src.stat().st_mtime:
            dest.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(src) as im:
                im.convert("RGB").save(dest, "PNG")
        return rel

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

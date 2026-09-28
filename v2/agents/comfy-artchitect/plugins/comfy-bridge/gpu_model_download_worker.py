"""Fixed, stdlib-only program shipped to the GPU. Model bytes never cross the daemon.

The portal runs this trusted source, never agent-supplied shell. A per-destination flock
deduplicates chats/retries; a partial file is not a Comfy model until verification succeeds.
"""

from __future__ import annotations

import json
from http.client import IncompleteRead
import os
import shutil
import struct
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from model_download_request import ModelDownloadRequest
from model_download_redirect_policy import ModelDownloadRedirectPolicy
from gpu_download_activity import GpuDownloadActivity
from gpu_download_progress_index import GpuDownloadProgressIndex
from model_download_resume_state import ModelDownloadResumeState
from parallel_range_download import ParallelRangeDownload
from model_storage_setup import ModelStorageSetup

if os.name == "posix":
    import fcntl


class GpuModelDownloadWorker:
    def __init__(self, root: Path, *, opener=None, clock=time.monotonic, sleep=time.sleep,
                 use_volume=False, storage=None):
        self.root = root.resolve()
        #: Where models go: ComfyUI's own models/ — or, on the person's own Vast machine with a
        #: volume attached, the volume (ModelStorageSetup), so they outlive the machine.
        self.use_volume = use_volume
        self.storage = storage or (lambda: ModelStorageSetup.for_machine(self.root))
        self.models = self.root / "models"
        # A test's fake opener, or None: then each download gets a fresh opener carrying the
        # redirect policy its SOURCE calls for (Hugging Face storage, or Civitai's).
        self.opener = opener
        self.clock = clock
        self.sleep = sleep

    def run(self, payload: dict, attempt_id: str = "") -> None:
        request = ModelDownloadRequest(**payload)
        if self.use_volume:
            # BEFORE the status folder exists: pointing ComfyUI at the volume may restart it, and
            # ComfyUI empties its temp folder on start. Unchanged after the first time.
            self.models = Path(self.storage().ensure()["models_dir"])
        status_dir = self.root / "temp" / "agentd-model-downloads"
        status_dir.mkdir(parents=True, exist_ok=True)
        activity = GpuDownloadActivity(
            status_dir, lock=lambda file: fcntl.flock(file, fcntl.LOCK_EX),
        )
        progress = GpuDownloadProgressIndex(status_dir, lock=lambda file: fcntl.flock(file, fcntl.LOCK_EX))
        status_path = status_dir / f"{request.job_id}.json"
        lock_path = status_dir / f"{request.job_id}.lock"
        # Locks are kernel-owned: a killed process cannot leave a permanent stale lock.
        with lock_path.open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            def report(state, **fields):
                data = {"state": state, "filename": request.filename,
                        "source_id": request.source_id, "attempt_id": attempt_id,
                        "updated_at": time.time(), **fields}
                temporary = status_path.with_suffix(".tmp")
                temporary.write_text(json.dumps(data), encoding="utf-8")
                temporary.replace(status_path)
                progress.update(request.job_id, data)
                # Keep the reaper's existing activity vocabulary stable across deployments.
                activity.update(request.job_id, "downloading" if state == "retrying" else state)
            try:
                report("starting")
                self.download(request, report)
                report("done")
            except Exception as error:
                # Do not send a signed CDN URL (HTTPError's str) into logs/status.
                message = (f"Source returned HTTP {error.code} from {urlsplit(error.url).hostname}; "
                           "a refusal does not establish that a provider key is missing or invalid"
                           if isinstance(error, urllib.error.HTTPError)
                           else str(error)[:400])
                report("failed", error=f"{type(error).__name__}: {message}",
                       http_status=error.code if isinstance(error, urllib.error.HTTPError) else None)

    def download(self, request: ModelDownloadRequest, report) -> None:
        models = self.models.resolve()
        if not models.is_dir():
            raise ValueError("ComfyUI models directory does not exist")
        folder = (models / request.directory).resolve()
        if not folder.is_relative_to(models):
            raise ValueError("Model directory escapes ComfyUI models")
        target = (folder / request.filename).resolve()
        if not target.is_relative_to(folder):
            raise ValueError("Model file escapes its models folder")
        folder = target.parent  # a subfoldered name (Krea2/lora.safetensors) lands in that subfolder
        folder.mkdir(parents=True, exist_ok=True)
        if target.is_symlink():
            raise ValueError("Refusing a symlink model destination")
        if target.exists():
            self.verify(target)
            return
        partial = target.with_suffix(target.suffix + ".agentd-part")
        if partial.is_symlink():
            raise ValueError("Refusing a symlink partial download")
        # A BIG FILE GOES OVER MANY CONNECTIONS (ParallelRangeDownload): one left by an earlier
        # run is picked up; otherwise the first response decides.
        split = (ParallelRangeDownload.saved(partial, request.source_id, clock=self.clock, sleep=self.sleep)
                 or self._single_stream(request, partial, target, folder, report))
        if split is None:
            return
        opener = self.opener or urllib.request.build_opener(ModelDownloadRedirectPolicy(request.source))
        try:
            split.run(request.url, request.headers(), opener.open, report,
                      free_bytes=shutil.disk_usage(folder).free - 256 * 1024 * 1024)
            report("verifying", received=split.total, total=split.total)
            self.verify(partial)
        except ValueError:
            split.discard()
            raise
        partial.replace(target)
        split.forget()

    def _single_stream(self, request: ModelDownloadRequest, partial: Path, target: Path, folder: Path,
                       report) -> ParallelRangeDownload | None:
        """One connection — or, when the first response shows a big file served in byte ranges,
        the split download to run instead (returned; nothing written). None = done."""
        resume = ModelDownloadResumeState(partial, request.source_id)
        try:
            for attempt in range(3):
                try:
                    resume = ModelDownloadResumeState(partial, request.source_id)
                    opener = self.opener or urllib.request.build_opener(
                        ModelDownloadRedirectPolicy(request.source)
                    )
                    http_request = urllib.request.Request(
                        request.url, headers={**request.headers(), **resume.headers()})
                    with opener.open(http_request, timeout=60) as response:
                        status = getattr(response, "status", 200)
                        size = resume.accept(status, response.headers)
                        split = ParallelRangeDownload.plan(partial, request.source_id, status, response.headers,
                                                           clock=self.clock, sleep=self.sleep)
                        if split is not None:
                            resume.reset()
                            return split
                        if size is not None and size <= 8:
                            raise ValueError("Source advertised an empty model file")
                        if "text/html" in str(response.headers.get("Content-Type", "")).lower():
                            raise ValueError("Source returned a web/login page instead of model weights")
                        available = (shutil.disk_usage(folder).free - 256 * 1024 * 1024
                                     + (partial.stat().st_size if partial.exists() else 0))
                        if available <= 0 or (size is not None and size > available):
                            raise ValueError("Not enough GPU disk space for this model")
                        read = resume.offset
                        started = last = self.clock()
                        initial = read
                        report("downloading", received=read, total=size, attempt=attempt + 1,
                               resumed_from=initial, bytes_per_second=0)
                        with partial.open("ab" if read else "wb") as output:
                            while chunk := response.read(1024 * 1024):
                                read += len(chunk)
                                if size is not None and read > size:
                                    raise ValueError("Source exceeded its Content-Length")
                                if read > available:
                                    raise ValueError("Not enough GPU disk space for this model")
                                output.write(chunk)
                                if self.clock() - last >= 5:
                                    report("downloading", received=read, total=size, attempt=attempt + 1,
                                           resumed_from=initial,
                                           bytes_per_second=(read - initial) / max(self.clock() - started, 0.001))
                                    last = self.clock()
                            output.flush()
                            os.fsync(output.fileno())
                        if size is not None and read != size:
                            raise OSError(f"Incomplete download: {read} of {size} bytes")
                    report("verifying", received=read, total=size)
                    self.verify(partial)
                    partial.replace(target)
                    resume.path.unlink(missing_ok=True)
                    return None
                except urllib.error.HTTPError as error:
                    if error.code == 416 and resume.offset:
                        resume.reset()
                        if attempt == 2:
                            raise
                    elif error.code not in (408, 429, 500, 502, 503, 504) or attempt == 2:
                        raise
                except (OSError, urllib.error.URLError, IncompleteRead):
                    if attempt == 2:
                        raise
                report("retrying", attempt=attempt + 2,
                       received=partial.stat().st_size if partial.exists() else 0,
                       total=resume.record.get("total"),
                       reason="transfer interrupted; resume if identity is confirmed, otherwise restart")
                self.sleep(2 ** attempt)
        except ValueError:
            # Invalid content or a mismatched range must never become a resumable prefix.
            resume.reset()
            raise
        finally:
            resume.retain_partial()

    @staticmethod
    def verify(path: Path) -> None:
        """Reject HTML/error pages, truncated headers and missing tensor bytes without loading tensors."""
        size = path.stat().st_size
        with path.open("rb") as source:
            prefix = source.read(8)
            if len(prefix) != 8:
                raise ValueError("Not a safetensors file")
            header_size = struct.unpack("<Q", prefix)[0]
            if not 2 <= header_size <= min(100_000_000, size - 8):
                raise ValueError("Invalid safetensors header size")
            header = json.loads(source.read(header_size))
        tensors = [value for key, value in header.items() if key != "__metadata__"]
        if not tensors:
            raise ValueError("Safetensors contains no tensors")
        end = 0
        for tensor in sorted(tensors, key=lambda item: item["data_offsets"][0]):
            start, stop = tensor["data_offsets"]
            if start != end or stop < start:
                raise ValueError("Invalid safetensors data offsets")
            end = stop
        if end != size - 8 - header_size:
            raise ValueError("Safetensors tensor bytes are incomplete")

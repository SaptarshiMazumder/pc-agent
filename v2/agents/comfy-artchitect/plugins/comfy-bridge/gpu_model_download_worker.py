"""Fixed, stdlib-only program shipped to the GPU. Model bytes never cross the daemon.

The portal runs this trusted source, never agent-supplied shell. A per-destination flock
deduplicates chats/retries; a partial file is not a Comfy model until verification succeeds.
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import time
import urllib.error
import urllib.request
from pathlib import Path

from model_download_request import ModelDownloadRequest
from model_download_redirect_policy import ModelDownloadRedirectPolicy

if os.name == "posix":
    import fcntl


class GpuModelDownloadWorker:
    def __init__(self, root: Path, *, opener=None, clock=time.monotonic, sleep=time.sleep):
        self.root = root.resolve()
        self.opener = opener or urllib.request.build_opener(ModelDownloadRedirectPolicy())
        self.clock = clock
        self.sleep = sleep

    def run(self, payload: dict, attempt_id: str = "") -> None:
        request = ModelDownloadRequest(**payload)
        status_dir = self.root / "temp" / "agentd-model-downloads"
        status_dir.mkdir(parents=True, exist_ok=True)
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
            try:
                report("starting")
                self.download(request, report)
                report("done")
            except Exception as error:
                # Do not send a signed CDN URL (HTTPError's str) into logs/status.
                message = (f"Source returned HTTP {error.code}" if isinstance(error, urllib.error.HTTPError)
                           else str(error)[:400])
                report("failed", error=f"{type(error).__name__}: {message}")

    def download(self, request: ModelDownloadRequest, report) -> None:
        models = (self.root / "models").resolve()
        if not models.is_dir():
            raise ValueError("ComfyUI models directory does not exist")
        folder = (models / request.directory).resolve()
        if not folder.is_relative_to(models):
            raise ValueError("Model directory escapes ComfyUI models")
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / request.filename
        if target.is_symlink():
            raise ValueError("Refusing a symlink model destination")
        if target.exists():
            self.verify(target)
            return
        partial = target.with_suffix(target.suffix + ".agentd-part")
        if partial.is_symlink():
            raise ValueError("Refusing a symlink partial download")
        try:
            for attempt in range(3):
                try:
                    # Restart interrupted transfers; never append bytes without validating
                    # Range/ETag semantics. Atomic final rename prevents partial loader entries.
                    with self.opener.open(request.url, timeout=60) as response:
                        size = int(response.headers.get("Content-Length") or 0)
                        if size <= 8:
                            raise ValueError("Source must advertise a nonempty Content-Length")
                        if size + 256 * 1024 * 1024 > shutil.disk_usage(folder).free + (partial.stat().st_size if partial.exists() else 0):
                            raise ValueError("Not enough GPU disk space for this model")
                        read = 0
                        last = self.clock()
                        report("downloading", received=0, total=size, attempt=attempt + 1)
                        with partial.open("wb") as output:
                            while chunk := response.read(1024 * 1024):
                                output.write(chunk)
                                read += len(chunk)
                                if read > size:
                                    raise ValueError("Source exceeded its Content-Length")
                                if self.clock() - last >= 5:
                                    report("downloading", received=read, total=size, attempt=attempt + 1)
                                    last = self.clock()
                            output.flush()
                            os.fsync(output.fileno())
                        if read != size:
                            raise OSError(f"Incomplete download: {read} of {size} bytes")
                    report("verifying", received=read, total=size)
                    self.verify(partial)
                    partial.replace(target)
                    return
                except urllib.error.HTTPError as error:
                    if error.code not in (408, 429, 500, 502, 503, 504) or attempt == 2:
                        raise
                except (OSError, urllib.error.URLError):
                    if attempt == 2:
                        raise
                self.sleep(2 ** attempt)
        finally:
            partial.unlink(missing_ok=True)

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

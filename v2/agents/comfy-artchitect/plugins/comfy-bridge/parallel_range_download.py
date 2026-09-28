"""Many connections for one big model file. Fixed, stdlib-only, shipped to the GPU with the worker.

WHY. One HTTP connection per file ran at 1.7–6.5 MiB/s from Hugging Face's CDN — a 21 GB
transformer took an hour on a host that could pull it in minutes. The cap is per connection,
not per host. So a big file is cut into parts, and up to CONNECTIONS of them download at once,
each over its own ranged request, each written at its own offset in one preallocated file.

PARTS, NOT ONE SLICE PER CONNECTION. Connections are not equally fast; with one fixed slice each
the file finishes when the slowest slice does. Small parts pulled from a shared queue let a fast
connection take more of them.

RESUMABLE ACROSS RUNS. What each part has received is saved beside the file (`.segments.json`)
every few seconds and on failure, with the source's identity; a new run of the worker picks the
same file up where it stopped, as long as the source is the same file (same source id, size and
strong ETag). A mismatch is never appended to: the record and the bytes are discarded.

Nothing here decides whether the bytes are a model — GpuModelDownloadWorker.verify does that
after the last part lands, exactly as for a single-stream download.
"""

from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
import urllib.error
import urllib.request
from http.client import IncompleteRead
from pathlib import Path

#: Below this a file is one request: the split costs more than it saves.
MIN_SIZE = 256 * 1024 * 1024
#: One part — a ranged request. Small enough that a slow connection cannot hold the tail.
PART_SIZE = 128 * 1024 * 1024
#: Connections at once, per file.
CONNECTIONS = 16
_RETRYABLE = (408, 429, 500, 502, 503, 504)
_STRONG_ETAG = re.compile(r'"[^"\r\n]+"')


class ParallelRangeDownload:
    def __init__(self, partial: Path, source_id: str, total: int, etag: str | None,
                 parts: list[list[int]] | None = None, *, clock=time.monotonic, sleep=time.sleep):
        self.partial = partial
        self.record_path = partial.with_suffix(partial.suffix + ".segments.json")
        self.source_id = source_id
        self.total = total
        self.etag = etag
        #: [start, end (inclusive), received] per part.
        self.parts = parts or [[s, min(s + PART_SIZE, total) - 1, 0] for s in range(0, total, PART_SIZE)]
        self.clock = clock
        self.sleep = sleep
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._errors: list[BaseException] = []

    # ── when to use it ──────────────────────────────────────────────────────────────────────

    @classmethod
    def plan(cls, partial: Path, source_id: str, status: int, headers, **kw) -> "ParallelRangeDownload | None":
        """From the first, whole-file response: a split download, when the file is big and the
        source says it serves byte ranges. None = keep the single stream."""
        if status != 200 or str(headers.get("Accept-Ranges", "")).lower() != "bytes":
            return None
        try:
            total = int(headers.get("Content-Length"))
        except (TypeError, ValueError):
            return None
        if total < MIN_SIZE:
            return None
        etag = headers.get("ETag")
        return cls(partial, source_id, total, etag if isinstance(etag, str) and _STRONG_ETAG.fullmatch(etag) else None, **kw)

    @classmethod
    def saved(cls, partial: Path, source_id: str, **kw) -> "ParallelRangeDownload | None":
        """A split download a previous run left, for the same source — or None (and any record
        that does not match is removed with its bytes)."""
        record_path = partial.with_suffix(partial.suffix + ".segments.json")
        if record_path.is_symlink():
            raise ValueError("Refusing a symlink resume record")
        try:
            record = json.loads(record_path.read_text())
        except (OSError, ValueError):
            return None
        parts = record.get("parts") if isinstance(record, dict) else None
        total = record.get("total") if isinstance(record, dict) else None
        ok = (isinstance(parts, list) and isinstance(total, int) and total > 0
              and record.get("source_id") == source_id
              and isinstance(record.get("etag"), str) and _STRONG_ETAG.fullmatch(record["etag"])
              and partial.exists() and partial.stat().st_size == total
              and all(isinstance(p, list) and len(p) == 3 and all(isinstance(x, int) for x in p)
                      and 0 <= p[2] <= p[1] - p[0] + 1 for p in parts))
        if not ok:
            record_path.unlink(missing_ok=True)
            partial.unlink(missing_ok=True)
            return None
        return cls(partial, source_id, total, record["etag"], parts, **kw)

    # ── the download ───────────────────────────────────────────────────────────────────────

    @property
    def received(self) -> int:
        with self._lock:
            return sum(p[2] for p in self.parts)

    def run(self, url: str, headers: dict, open_url, report, free_bytes: int) -> None:
        """Download every part not yet received. Raises the first failure after every
        connection has stopped; the record is saved either way (unless the failure says the
        bytes are wrong — ValueError — then the caller discards them)."""
        start_received = self.received
        if free_bytes < self.total - start_received:
            raise ValueError("Not enough GPU disk space for this model")
        fd = os.open(self.partial, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            if os.fstat(fd).st_size != self.total:
                os.ftruncate(fd, self.total)
            todo: queue.Queue = queue.Queue()
            for part in self.parts:
                if part[2] < part[1] - part[0] + 1:
                    todo.put(part)
            workers = [threading.Thread(target=self._worker, args=(todo, fd, url, headers, open_url), daemon=True)
                       for _ in range(min(CONNECTIONS, todo.qsize()))]
            started = self.clock()
            for w in workers:
                w.start()
            report("downloading", received=start_received, total=self.total, attempt=1,
                   resumed_from=start_received, bytes_per_second=0, connections=len(workers))
            while any(w.is_alive() for w in workers):
                for w in workers:
                    w.join(timeout=5 / max(len(workers), 1))
                self._save()
                got = self.received
                report("downloading", received=got, total=self.total, attempt=1, resumed_from=start_received,
                       bytes_per_second=(got - start_received) / max(self.clock() - started, 0.001),
                       connections=len(workers))
            os.fsync(fd)
        finally:
            os.close(fd)
            self._save()
        if self._errors:
            raise self._errors[0]
        if self.received != self.total:
            raise OSError(f"Incomplete download: {self.received} of {self.total} bytes")

    def _worker(self, todo: queue.Queue, fd: int, url: str, headers: dict, open_url) -> None:
        while not self._stop.is_set():
            try:
                part = todo.get_nowait()
            except queue.Empty:
                return
            try:
                self._fetch(part, fd, url, headers, open_url)
            except BaseException as error:  # noqa: BLE001 — handed to run(), which raises it
                with self._lock:
                    self._errors.append(error)
                self._stop.set()
                return

    def _fetch(self, part: list[int], fd: int, url: str, headers: dict, open_url) -> None:
        start, end = part[0], part[1]
        for attempt in range(4):
            if self._stop.is_set():
                return
            offset = start + part[2]
            if offset > end:
                return
            wanted = {**headers, "Range": f"bytes={offset}-{end}"}
            if self.etag:
                wanted["If-Range"] = self.etag
            try:
                with open_url(urllib.request.Request(url, headers=wanted), timeout=60) as response:
                    self._accept(getattr(response, "status", 200), response.headers, offset, end)
                    while not self._stop.is_set() and (chunk := response.read(1024 * 1024)):
                        at = start + part[2]
                        if at + len(chunk) - 1 > end:
                            raise ValueError("Source sent more than the requested range")
                        os.pwrite(fd, chunk, at)
                        with self._lock:
                            part[2] += len(chunk)
                if self._stop.is_set() or start + part[2] > end:
                    return
                raise OSError("range ended early")
            except urllib.error.HTTPError as error:
                if error.code not in _RETRYABLE or attempt == 3:
                    raise
            except (OSError, urllib.error.URLError, IncompleteRead):
                if attempt == 3:
                    raise
            self.sleep(2 ** attempt)

    def _accept(self, status: int, headers, offset: int, end: int) -> None:
        """Only the exact range of the same file may be written."""
        if status != 206:
            raise ValueError(f"Source answered a byte range with HTTP {status} (file changed or ranges refused)")
        match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", headers.get("Content-Range", ""))
        if not match or tuple(map(int, match.groups())) != (offset, end, self.total):
            raise ValueError("Partial response does not match the requested range")
        if self.etag and headers.get("ETag") not in (None, self.etag):
            raise ValueError("Source file changed during the download")

    # ── the record ─────────────────────────────────────────────────────────────────────────

    def _save(self) -> None:
        """Only a source with a strong ETag is resumable: without one, a later run cannot tell
        the same file from a new one."""
        if not self.etag:
            return
        with self._lock:
            record = {"source_id": self.source_id, "etag": self.etag, "total": self.total,
                      "parts": [list(p) for p in self.parts]}
        temporary = self.record_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(record))
        temporary.replace(self.record_path)

    def forget(self) -> None:
        """The file is complete and moved into place."""
        self.record_path.unlink(missing_ok=True)

    def discard(self) -> None:
        """The bytes are wrong: nothing of them is kept to resume from."""
        self.record_path.unlink(missing_ok=True)
        self.partial.unlink(missing_ok=True)

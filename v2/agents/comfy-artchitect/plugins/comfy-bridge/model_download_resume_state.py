"""Persist and validate partial-transfer identity without storing provider URLs or keys."""

import json
import re


class ModelDownloadResumeState:
    def __init__(self, partial, source_id):
        self.partial = partial
        self.path = partial.with_suffix(partial.suffix + ".json")
        self.source_id = source_id
        if self.path.is_symlink():
            raise ValueError("Refusing a symlink resume record")
        try:
            self.record = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.record = {}
        if not isinstance(self.record, dict):
            self.record = {}
        self.offset = partial.stat().st_size if partial.exists() else 0
        total = self.record.get("total")
        if (self.record.get("source_id") != source_id
                or not self.strong_etag(self.record.get("etag"))
                or not isinstance(total, int) or not 0 < self.offset < total):
            self.reset()

    @staticmethod
    def strong_etag(value):
        return isinstance(value, str) and re.fullmatch(r'"[^"\r\n]+"', value) is not None

    def reset(self):
        self.partial.unlink(missing_ok=True)
        self.path.unlink(missing_ok=True)
        self.record, self.offset = {}, 0

    def headers(self):
        if not self.offset:
            return {}
        return {"Range": f"bytes={self.offset}-", "If-Range": self.record["etag"]}

    def accept(self, status, headers):
        """Return full size. Only a matching 206 is allowed to append to old bytes."""
        etag = headers.get("ETag")
        length = headers.get("Content-Length")
        length = int(length) if length is not None else None
        if status == 206:
            match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", headers.get("Content-Range", ""))
            if not self.offset or not match:
                raise ValueError("Unrequested or invalid partial response")
            start, end, total = map(int, match.groups())
            if (start != self.offset or end != total - 1
                    or total != self.record["total"] or etag != self.record["etag"]
                    or (length is not None and length != end - start + 1)):
                raise ValueError("Partial response does not match saved file identity/range")
            return total
        if status != 200:
            raise ValueError(f"Unexpected download response: HTTP {status}")
        # Range ignored or If-Range changed: use the full new body, never append it.
        self.reset()
        if self.strong_etag(etag) and length is not None and length > 8:
            self.record = {"source_id": self.source_id, "etag": etag, "total": length}
            self.path.write_text(json.dumps(self.record))
        return length

    def retain_partial(self):
        if not self.record or not self.partial.exists():
            self.reset()

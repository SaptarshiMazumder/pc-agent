"""A provider's result URL -> a file in the workspace, straight away.

Both providers' links expire (BytePlus: 24 h, and at most 100 downloads), so a result that is
not saved when it arrives is a result that was paid for and lost.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from urllib.parse import urlsplit

from agent_runtime.infrastructure.net.outbound import fetch

_MEDIA_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
}


class MediaDownloader:
    def __init__(self, max_bytes: int, timeout_s: float) -> None:
        self._max_bytes = max_bytes
        self._timeout_s = timeout_s

    def save(self, url: str, stem: str, content_type: str, default_ext: str) -> str:
        """Download `url` to `<stem><ext>` (workspace-relative); returns that path."""
        ext = _MEDIA_TYPES.get((content_type or "").split(";")[0].strip().lower())
        if not ext:
            suffix = PurePosixPath(urlsplit(url).path).suffix.lower()
            ext = suffix if suffix in _MEDIA_TYPES.values() else default_ext
        dest = stem + ext
        res = fetch(url, save_path=dest, max_bytes=self._max_bytes, timeout_s=self._timeout_s)
        if not res.ok:
            raise RuntimeError(
                f"could not download the result ({res.error or f'HTTP {res.status}: {res.text[:300]}'})"
            )
        return dest

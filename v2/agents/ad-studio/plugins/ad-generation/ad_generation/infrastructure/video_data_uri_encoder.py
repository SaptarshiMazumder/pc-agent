"""A workspace clip -> a `data:` URI, the way fal takes an input video inline (a clip to edit or
continue). Sent as it is: the clip is the user's, and re-encoding it would change it."""

from __future__ import annotations

import base64

from ad_generation.infrastructure.run_workspace import RunWorkspace

_TYPES = {".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm"}


class VideoDataUriEncoder:
    def __init__(self, workspace: RunWorkspace) -> None:
        self._ws = workspace

    def encode(self, rel: str) -> str:
        path = self._ws.path(rel)
        kind = _TYPES.get(path.suffix.lower())
        if kind is None:
            raise ValueError(f"{rel}: a clip is mp4, mov or webm")
        return f"data:{kind};base64," + base64.b64encode(path.read_bytes()).decode("ascii")

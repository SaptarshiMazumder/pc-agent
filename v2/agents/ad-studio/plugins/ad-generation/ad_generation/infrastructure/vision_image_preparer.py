"""Workspace images -> small JPEG copies for a vision model to read.

WHY. A check sends the judged still, every product photo and the cast sheet in one request. At
full size that was ~10 MB of base64 through the model proxy per check — five checks in
parallel took the proxy down (502). A judge needs no more than ~1024 px to see a logo, a colour
or a face; the copies are cached by content, so the same photo is shrunk once.
"""

from __future__ import annotations

import hashlib

from PIL import Image

from ad_generation.infrastructure.run_workspace import RunWorkspace

CACHE = "campaigns/.vision"


class VisionImagePreparer:
    def __init__(self, workspace: RunWorkspace, max_side: int, quality: int) -> None:
        self._ws = workspace
        self._max_side = max_side
        self._quality = quality

    def prepare(self, images: list[str]) -> list[str]:
        """Absolute paths of the small copies, in the same order as `images`."""
        out = []
        for rel in images:
            src = self._ws.path(rel)
            if not src.is_file():
                raise FileNotFoundError(f"no image at {rel}")
            digest = hashlib.sha1(src.read_bytes()).hexdigest()[:16]
            dest = self._ws.path(f"{CACHE}/{digest}-{self._max_side}.jpg")
            if not dest.is_file():
                dest.parent.mkdir(parents=True, exist_ok=True)
                with Image.open(src) as im:
                    im = im.convert("RGB")
                    im.thumbnail((self._max_side, self._max_side), Image.LANCZOS)
                    im.save(dest, format="JPEG", quality=self._quality)
            out.append(str(dest))
        return out

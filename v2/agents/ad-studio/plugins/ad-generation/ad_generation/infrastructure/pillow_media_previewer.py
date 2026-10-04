"""MediaPreviewer with Pillow: <stem>.preview.jpg beside the image, made once."""

from __future__ import annotations

from PIL import Image

from ad_generation.infrastructure.run_workspace import RunWorkspace


class PillowMediaPreviewer:
    def __init__(self, workspace: RunWorkspace, max_side: int, quality: int) -> None:
        self._ws = workspace
        self._max_side = max_side
        self._quality = quality

    def preview(self, path: str) -> str:
        rel = path.rsplit(".", 1)[0] + ".preview.jpg"
        src, dest = self._ws.path(path), self._ws.path(rel)
        if not dest.is_file() or dest.stat().st_mtime < src.stat().st_mtime:
            with Image.open(src) as im:
                im = im.convert("RGB")
                im.thumbnail((self._max_side, self._max_side))
                im.save(dest, "JPEG", quality=self._quality)
        return rel

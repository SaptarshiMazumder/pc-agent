"""ImageResizer with Pillow: a high-quality Lanczos resample, written back as the same format."""

from __future__ import annotations

from PIL import Image

from ad_generation.infrastructure.run_workspace import RunWorkspace


class PillowImageResizer:
    def __init__(self, workspace: RunWorkspace) -> None:
        self._ws = workspace

    def match(self, path: str, like: str) -> None:
        target = self._ws.path(path)
        with Image.open(self._ws.path(like)) as ref:
            size = ref.size
        with Image.open(target) as im:
            if im.size == size:
                return
            resized = im.convert("RGB").resize(size, Image.LANCZOS)
        resized.save(target)

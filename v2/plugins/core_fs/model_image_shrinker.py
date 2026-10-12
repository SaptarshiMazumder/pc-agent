"""An image made the size a model needs before `read` puts it into the conversation.

Models see an image at most ~1568 px on its long edge (Claude's API resizes anything larger, and
other providers do the same), so sending a 6000 px camera photo costs upload time and request size
on EVERY later turn — the picture stays in the history — and shows the model nothing more. Larger
images are resized to that edge (EXIF orientation applied) and re-encoded: JPEG, or PNG when the
image has transparency. An image already within the edge and small in bytes is sent untouched.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageOps


@dataclass(frozen=True)
class ShrunkImage:
    data: bytes
    mime: str  # "" = the original bytes, sent as they are (the caller knows their type)
    size: tuple[int, int]  # what the model sees
    original: tuple[int, int]

    @property
    def resized(self) -> bool:
        return self.size != self.original


class ModelImageShrinker:
    def __init__(self, max_edge: int = 1568, pass_bytes: int = 1_500_000, jpeg_quality: int = 85) -> None:
        self._max_edge = max_edge
        self._pass_bytes = pass_bytes
        self._jpeg_quality = jpeg_quality

    def shrink(self, data: bytes) -> ShrunkImage:
        """Raises the decoder's error for a file that is not a readable image."""
        with Image.open(io.BytesIO(data)) as opened:
            image = ImageOps.exif_transpose(opened)
            original = image.size
            if max(original) <= self._max_edge and len(data) <= self._pass_bytes:
                return ShrunkImage(data, "", original, original)
            image.thumbnail((self._max_edge, self._max_edge), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
                image.save(out, "PNG", optimize=True)
                mime = "image/png"
            else:
                image.convert("RGB").save(out, "JPEG", quality=self._jpeg_quality, optimize=True)
                mime = "image/jpeg"
            return ShrunkImage(out.getvalue(), mime, image.size, original)

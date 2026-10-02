"""A workspace image -> a `data:` URI both providers accept as an input image.

WHY INLINE. fal documents no REST upload endpoint (only its SDK), and BytePlus takes data URIs
natively. Inline keeps one path for both. The image is re-encoded as a JPEG no larger than
`max_side` on its long edge, so a 12 MP phone photo becomes a few hundred KB of request body
instead of tens of MB — both providers downscale larger inputs anyway.
"""

from __future__ import annotations

import base64
import io

from PIL import Image

from ad_generation.infrastructure.run_workspace import RunWorkspace


class ImageDataUriEncoder:
    def __init__(self, workspace: RunWorkspace, max_side: int, quality: int) -> None:
        self._workspace = workspace
        self._max_side = max_side
        self._quality = quality

    def encode(self, rel: str) -> str:
        path = self._workspace.path(rel)
        if not path.is_file():
            raise FileNotFoundError(f"no image at {rel}")
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((self._max_side, self._max_side), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=self._quality)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")

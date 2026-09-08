"""Pillow implementation of the image-thumbnail generator port."""

from __future__ import annotations

import warnings
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps

from agent_runtime.application.interfaces.image_thumbnail_generator import (
    GeneratedImageThumbnail,
    ImageThumbnailGenerationError,
    ImageThumbnailTooLargeError,
)


class PillowImageThumbnailGenerator:
    """Decode a bounded raster source and emit a metadata-free WebP preview."""

    _SUPPORTED_FORMATS = frozenset({"BMP", "GIF", "ICO", "JPEG", "PNG", "TIFF", "WEBP"})

    def __init__(
        self,
        *,
        max_source_bytes: int = 128 * 1024 * 1024,
        max_source_pixels: int = 40_000_000,
        max_source_dimension: int = 20_000,
        webp_quality: int = 78,
    ):
        if min(max_source_bytes, max_source_pixels, max_source_dimension) < 1:
            raise ValueError("image safety limits must be positive")
        if not 1 <= webp_quality <= 100:
            raise ValueError("webp_quality must be between 1 and 100")
        self._max_source_bytes = max_source_bytes
        self._max_source_pixels = max_source_pixels
        self._max_source_dimension = max_source_dimension
        self._webp_quality = webp_quality

    def generate(self, source: Path, *, max_edge: int) -> GeneratedImageThumbnail:
        try:
            if source.stat().st_size > self._max_source_bytes:
                raise ImageThumbnailTooLargeError("image file exceeds thumbnail byte limit")

            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(source) as opened:
                    if opened.format not in self._SUPPORTED_FORMATS:
                        raise ImageThumbnailGenerationError("unsupported image format")
                    width, height = opened.size
                    if width < 1 or height < 1:
                        raise ImageThumbnailGenerationError("image has invalid dimensions")
                    if max(width, height) > self._max_source_dimension:
                        raise ImageThumbnailTooLargeError(
                            "image dimensions exceed thumbnail dimension limit"
                        )
                    if width * height > self._max_source_pixels:
                        raise ImageThumbnailTooLargeError(
                            "image dimensions exceed thumbnail pixel limit"
                        )

                    # Animated GIF/WebP previews intentionally use frame zero. Resize before
                    # copying so Pillow can use format-specific draft decoding (notably JPEG),
                    # then normalize camera orientation. Rotation cannot break the max-edge bound.
                    opened.seek(0)
                    opened.thumbnail(
                        (max_edge, max_edge), Image.Resampling.LANCZOS, reducing_gap=3.0
                    )
                    frame = ImageOps.exif_transpose(opened).copy()
                    has_alpha = "A" in frame.getbands() or "transparency" in opened.info
                    frame = frame.convert("RGBA" if has_alpha else "RGB")

                    output = BytesIO()
                    frame.save(
                        output,
                        format="WEBP",
                        quality=self._webp_quality,
                        method=4,
                    )
                    return GeneratedImageThumbnail(
                        data=output.getvalue(),
                        mime_type="image/webp",
                        width=frame.width,
                        height=frame.height,
                    )
        except ImageThumbnailGenerationError:
            raise
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise ImageThumbnailTooLargeError("image exceeds Pillow's safe decode limit") from exc
        except (OSError, ValueError, EOFError) as exc:
            raise ImageThumbnailGenerationError("image cannot be decoded safely") from exc

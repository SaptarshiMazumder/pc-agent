"""PillowImageThumbnailGenerator: bounded raster decoding and WebP output."""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from agent_runtime.application.interfaces.image_thumbnail_generator import (
    ImageThumbnailGenerationError,
    ImageThumbnailTooLargeError,
)
from agent_runtime.infrastructure.images.pillow_image_thumbnail_generator import (
    PillowImageThumbnailGenerator,
)


def test_large_image_becomes_a_512px_metadata_free_webp(tmp_path):
    source = tmp_path / "large.png"
    Image.new("RGB", (2048, 1024), "#d45b43").save(source, pnginfo=None)
    generator = PillowImageThumbnailGenerator()

    result = generator.generate(source, max_edge=512)

    assert result.mime_type == "image/webp"
    assert (result.width, result.height) == (512, 256)
    with Image.open(BytesIO(result.data)) as preview:
        assert preview.format == "WEBP"
        assert preview.size == (512, 256)
        assert not preview.getexif()


def test_exif_orientation_is_applied(tmp_path):
    source = tmp_path / "portrait.jpg"
    exif = Image.Exif()
    exif[274] = 6  # rotate 90 degrees clockwise
    Image.new("RGB", (200, 100), "red").save(source, exif=exif)

    result = PillowImageThumbnailGenerator().generate(source, max_edge=100)

    assert (result.width, result.height) == (50, 100)


def test_animated_image_uses_only_its_first_frame(tmp_path):
    source = tmp_path / "animated.gif"
    red = Image.new("RGB", (20, 20), "red")
    blue = Image.new("RGB", (20, 20), "blue")
    red.save(source, save_all=True, append_images=[blue], duration=100, loop=0)

    result = PillowImageThumbnailGenerator().generate(source, max_edge=10)

    with Image.open(BytesIO(result.data)) as preview:
        r, _g, b = preview.convert("RGB").getpixel((5, 5))
        assert r > b
        assert not getattr(preview, "is_animated", False)


@pytest.mark.parametrize(
    ("limits", "size"),
    [
        ({"max_source_bytes": 1}, (20, 20)),
        ({"max_source_pixels": 100}, (20, 20)),
        ({"max_source_dimension": 10}, (20, 5)),
    ],
)
def test_byte_pixel_and_dimension_limits_fail_closed(tmp_path, limits, size):
    source = tmp_path / "too-large.png"
    Image.new("RGB", size, "red").save(source)

    with pytest.raises(ImageThumbnailTooLargeError):
        PillowImageThumbnailGenerator(**limits).generate(source, max_edge=10)


@pytest.mark.parametrize(
    ("name", "content"),
    [("not-image.png", b"not an image"), ("vector.svg", b"<svg></svg>")],
)
def test_corrupt_and_unsupported_images_fail_cleanly(tmp_path, name, content):
    source = tmp_path / name
    source.write_bytes(content)

    with pytest.raises(ImageThumbnailGenerationError):
        PillowImageThumbnailGenerator().generate(source, max_edge=512)

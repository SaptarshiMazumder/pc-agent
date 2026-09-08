"""Image-preview infrastructure composition."""

from agent_runtime.application.services.image_thumbnail_service import ImageThumbnailService
from agent_runtime.infrastructure.images.pillow_image_thumbnail_generator import (
    PillowImageThumbnailGenerator,
)

__all__ = ["PillowImageThumbnailGenerator", "build_image_thumbnail_service"]


def build_image_thumbnail_service() -> ImageThumbnailService:
    return ImageThumbnailService(PillowImageThumbnailGenerator(), max_edge=512)

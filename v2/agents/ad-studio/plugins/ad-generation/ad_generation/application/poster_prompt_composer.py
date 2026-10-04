"""A text ad's design fields + its copy -> its image prompt and motion prompt.

Assembled, not written, like a photo ad's (PromptComposer): the writer chooses the design as
fields, and the copy goes in exactly — each piece quoted, with its role — from prompts/
keyframe_poster.md and motion_poster.md. A field the template names that is missing is a
KeyError, never a prompt with a hole in it.
"""

from __future__ import annotations

from ad_generation.domain.poster_copy import PosterCopy
from ad_generation.domain.poster_layout import clear_space
from ad_generation.domain.product_profile import ProductProfile


class PosterPromptComposer:
    def __init__(self, keyframe: str, motion: str, overlay: str) -> None:
        self._keyframe = keyframe
        self._motion = motion
        self._overlay = overlay

    def compose(
        self, spec: dict, copy: PosterCopy, product: ProductProfile, aspect_ratio: str, format_key: str
    ) -> tuple[str, str, str]:
        """-> (the design with its words drawn by the model, its motion, the picture alone for overlay)."""
        values = {
            **{k: str(v).strip().rstrip(".") for k, v in spec.items()},
            "aspect_ratio": aspect_ratio,
            "product_name": product.name,
            "product_description": product.description,
            "copy": copy.placed(),
            "clear_space": clear_space(format_key),
        }
        return (
            self._keyframe.format_map(values).strip(),
            self._motion.format_map(values).strip(),
            self._overlay.format_map(values).strip(),
        )

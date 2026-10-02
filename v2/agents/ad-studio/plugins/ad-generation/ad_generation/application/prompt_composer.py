"""The look + one shot's checklist -> that shot's still prompt and motion prompt.

THE PROMPT IS ASSEMBLED, NOT WRITTEN. The brief writer makes the creative choices as fields;
this fills them into fixed templates (prompts/keyframe_*.md, prompts/motion_*.md), so every still
states outfit, place, time, weather, pose, mood, background, framing and light — the same items,
in the same order, every time — and every clip is told to keep the look. A field the template
names that the values do not hold is a KeyError, never a prompt with a hole in it.
"""

from __future__ import annotations

from ad_generation.domain.product_profile import ProductProfile


class PromptComposer:
    def __init__(self, keyframe_cast: str, keyframe_product: str, motion_cast: str, motion_product: str) -> None:
        self._keyframe = {True: keyframe_cast, False: keyframe_product}
        self._motion = {True: motion_cast, False: motion_product}

    def compose(self, look: dict, spec: dict, cast_name: str, product: ProductProfile) -> tuple[str, str]:
        has_cast = bool(cast_name)
        # The templates end each field with their own punctuation; a writer's trailing stop
        # would double it.
        values = {
            **{k: str(v).strip().rstrip(".") for k, v in look.items()},
            **{k: str(v).strip().rstrip(".") for k, v in spec.items()},
            "cast_name": cast_name,
            "product_name": product.name,
            "product_description": product.description,
        }
        return (
            self._keyframe[has_cast].format_map(values).strip(),
            self._motion[has_cast].format_map(values).strip(),
        )

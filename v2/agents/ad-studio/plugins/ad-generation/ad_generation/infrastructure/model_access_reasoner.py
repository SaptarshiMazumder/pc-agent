"""The Reasoner port over the runtime's model access (`tool.models`) — the agent's own models,
resolved from config per tool. JSON in, JSON out; images go small (VisionImagePreparer)."""

from __future__ import annotations

import json
import re

from ad_generation.infrastructure.vision_image_preparer import VisionImagePreparer


def _json(text: str) -> dict:
    # Models wrap JSON in a ```json fence often enough that refusing it would be pedantry; any
    # other shape is an error the caller reports.
    stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    try:
        data = json.loads(stripped)
    except ValueError as e:
        raise ValueError(f"the model did not answer in JSON ({e}): {text[:300]}") from e
    # A one-item list holding the object IS the object — Gemini's JSON mode answers that way at
    # times, and refusing it turned passing checks into errors.
    if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
        data = data[0]
    if not isinstance(data, dict):
        raise ValueError(f"the model answered JSON that is not an object: {text[:300]}")
    return data


class ModelAccessReasoner:
    def __init__(self, models, model: str, images: VisionImagePreparer, timeout_s: float) -> None:
        self._models = models
        self._model = model
        self._images = images
        self._timeout_s = timeout_s

    def read_images(self, prompt: str, images: list[str]) -> dict:
        return _json(
            self._models.vision(
                model=self._model,
                prompt=prompt,
                image_paths=self._images.prepare(images),
                want_json=True,
                timeout=self._timeout_s,
            )
        )

    def think(self, prompt: str) -> dict:
        return _json(self._models.text(model=self._model, prompt=prompt, timeout=self._timeout_s))

"""The design reference library: keep a picture of a design worth following — the agent's model
reads off it what a designer would (layout, photo shapes, type, palette, decoration, where the
words go, what it suits) — list them, drop one.

A picture that is not ONE design (a page of many, a website, an empty screen) is refused, so the
library only ever holds designs to follow. Saving costs one vision call; listing and dropping are
free."""

from __future__ import annotations

import json

from ad_generation.application.interfaces.design_reference_store import DesignReferenceStore
from ad_generation.application.interfaces.reasoner import Reasoner
from ad_generation.domain.design_reference import SPEC_KEYS, DesignReference


class DesignReferenceService:
    def __init__(self, references: DesignReferenceStore, reader: Reasoner | None, read_prompt: str, clock) -> None:
        self._references = references
        self._reader = reader
        self._prompt = read_prompt
        self._clock = clock

    def all(self) -> list[DesignReference]:
        return self._references.all()

    def add(self, image: str, name: str, source: str, crop: list[int] | None, notes: str) -> DesignReference:
        if self._reader is None:
            raise RuntimeError("reading a reference needs the agent's model")
        if crop is not None and len(crop) != 4:
            raise ValueError("crop is [x, y, width, height] in the picture's pixels")
        slug = self._references.new_slug(name or "reference")
        kept = self._references.import_image(slug, image, tuple(int(v) for v in crop) if crop else None)
        try:
            fingerprint = self._references.fingerprint(kept)
            twin = next((r for r in self._references.all() if r.fingerprint == fingerprint), None)
            if twin:
                raise ValueError(f"that picture is already in the library as '{twin.slug}'")
            answer = self._reader.read_images(
                self._prompt + "\n\nFACTS:\n" + json.dumps({"name": name or None, "notes": notes or None}, ensure_ascii=False), [kept]
            )
            if not answer.get("one_design"):
                raise ValueError(f"not one design to follow — {answer.get('why') or 'the picture shows something else'}; crop to one design")
            spec = {k: str(answer.get("spec", {}).get(k) or "") for k in SPEC_KEYS}
            if not spec["layout"]:
                raise ValueError("the reading came back without a layout")
        except Exception:
            self._references.discard(slug)
            raise
        reference = DesignReference(
            slug=slug, name=(name or str(answer.get("name") or slug)).strip(), image=kept, source=source.strip(),
            suits=tuple(str(t).strip().lower() for t in answer.get("suits") or () if str(t).strip()), spec=spec,
            created=self._clock(), fingerprint=fingerprint,
        )
        self._references.save(reference)
        return reference

    def delete(self, slug: str) -> None:
        self._references.delete(slug)

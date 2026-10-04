"""A user's model choices, checked against the model specs BEFORE anything runs or is paid for:
a model no spec knows, an edit-only model asked to make something, a clip length or resolution
the model cannot make."""

from __future__ import annotations

from ad_generation.infrastructure.model_spec_book import ModelSpecBook


class GenerationChoiceValidator:
    def __init__(self, specs: ModelSpecBook) -> None:
        self._specs = specs

    @staticmethod
    def _split(choice: str, what: str) -> tuple[str, str]:
        if "/" not in choice:
            raise ValueError(f"{what} must be provider/model, not '{choice}'")
        provider, model = choice.split("/", 1)
        return provider, model

    def image(self, choice: str, action: str, references: int = 0) -> None:
        """An image model for an images step (needs the person AND the product: 2+ references)
        or a shoot sheet (one reference will do) — and one that takes as many references as the
        run will send."""
        provider, model = self._split(choice, "model")
        spec = self._specs.spec(provider, model, "image")  # raises, naming the known models
        if spec.get("fix_only"):
            raise ValueError(f"{choice} is an edit mode for fixing an image (still_fix), not for making one")
        if action in ("images", "product_sheet") and int(spec.get("max_references") or 99) < 2:
            raise ValueError(f"{choice} takes one reference image; an ad image needs the person and the product")
        limit = spec.get("max_references")
        if limit and references > int(limit):
            raise ValueError(
                f"{choice} takes at most {limit} reference images and this run sends {references} — "
                "pick a model that takes more, or send fewer references"
            )

    def video(self, choice: str, seconds: int, resolution: str) -> None:
        provider, model = self._split(choice, "model")
        spec = self._specs.spec(provider, model, "video")  # raises, naming the known models
        if spec.get("edit_only"):
            raise ValueError(f"{choice} only edits a clip (clip_edit); it cannot make one")
        allowed = spec.get("durations")
        if seconds and allowed and not allowed["min"] <= seconds <= allowed["max"]:
            raise ValueError(f"{choice} makes {allowed['min']}-{allowed['max']} s clips, not {seconds} s")
        offered = list((spec.get("resolutions") or {}).keys())
        if resolution and offered and resolution not in offered:
            raise ValueError(f"{choice} makes {' / '.join(offered)}, not {resolution}")

    def resolution_for(self, choice: str, resolution: str) -> str:
        """The resolution a clip will be made at: the one asked for, or — a model that makes only
        one — that one."""
        provider, model = self._split(choice, "model")
        offered = list((self._specs.spec(provider, model, "video").get("resolutions") or {}).keys())
        if len(offered) == 1:
            return offered[0]
        return resolution

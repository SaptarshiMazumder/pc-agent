"""A model spec's `price` block -> USD for one result.

Three shapes, as the providers publish them:
  {"per_image": x, "per_extra_reference": y}   the first reference image is free
  {"per_second": {"720p": x, "any": x}, "audio_factor": f}
  {"per_million_tokens": {"720p": x}}          BytePlus video, billed on reported usage
"""

from __future__ import annotations


class PriceCalculator:
    def image(self, spec: dict, references: int) -> float:
        price = self._price(spec)
        return float(price["per_image"]) + float(price.get("per_extra_reference", 0)) * max(0, references - 1)

    def video_seconds(self, spec: dict, resolution: str, seconds: float, audio: bool) -> float:
        price = self._price(spec)
        rates = price["per_second"]
        rate = rates.get(resolution, rates.get("any"))
        if rate is None:
            raise KeyError(f"no per-second price for {resolution} (priced: {', '.join(rates)})")
        factor = float(price.get("audio_factor", 1.0)) if audio else 1.0
        return float(rate) * seconds * factor

    def video_tokens(self, spec: dict, resolution: str, tokens: int) -> float:
        rates = self._price(spec)["per_million_tokens"]
        if resolution not in rates:
            raise KeyError(f"no token price for {resolution} (priced: {', '.join(rates)})")
        return float(rates[resolution]) * tokens / 1_000_000

    @staticmethod
    def _price(spec: dict) -> dict:
        price = spec.get("price")
        if not isinstance(price, dict):
            raise KeyError("this model spec has no price block")
        return price

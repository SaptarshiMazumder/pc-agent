"""SeedreamPriceEstimator — what a Seedream job is expected to cost, before it runs.

The approval card and the Stages panel show it next to the Run button. On Higgsfield (the
preferred backend) a picture is a fixed number of the plan's credits (`credits_per_image` in its
model spec) at the plan's dollar price per credit; on fal it is the published price table. The
charge itself is the provider's exact figure (SeedreamImageService), not this estimate.
"""

from __future__ import annotations

from collections.abc import Callable

from image_generation.model_spec_book import ModelSpecBook
from image_generation.price_calculator import PriceCalculator


class SeedreamPriceEstimator:
    def __init__(self, specs: ModelSpecBook, prices: PriceCalculator, provider: str, model: str,
                 higgsfield_usd_per_credit: Callable[[], float]) -> None:
        self._specs = specs
        self._prices = prices
        self._provider = provider
        self._model = model
        self._usd_per_credit = higgsfield_usd_per_credit

    def usd(self, count: int, references: int) -> float:
        spec = self._specs.spec(self._provider, self._model, "image")
        if self._provider == "higgsfield":
            return float(spec["credits_per_image"]) * float(self._usd_per_credit()) * count
        return self._prices.image(spec, references) * count


__all__ = ["SeedreamPriceEstimator"]

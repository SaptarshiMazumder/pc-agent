"""SeedreamImageService — the agent's own image making: Seedream 5 Pro, straight from the provider.

No ComfyUI. Realistic pictures (people, products, places) are what a good video starts from, and
the money for them is better spent on Seedream than on a free model that needs a head swap and
a realism pass to come close. One job:

  1. what the person's credits cover is read (PlatformCreditsGateway) and handed to the adapter,
     which refuses BEFORE submitting a job that would cost more (BudgetExceeded);
  2. Higgsfield makes it (exact cost, from its own credits); when Higgsfield is unavailable — an
     expired login token, an outage — fal makes it instead, and the switch is said;
  3. the real cost is debited. A charge the platform refuses does not lose the pictures: they
     are already paid for and saved, and the failure is reported with them.

A refusal by the provider (moderation, a likeness filter) is not "unavailable": the same input
would be refused by the fallback too, so it is raised, not retried.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from image_generation.budget_exceeded import BudgetExceeded
from image_generation.generated_media import GeneratedMedia
from image_generation.image_generator import ImageGenerator
from image_generation.image_request import ImageRequest
from image_generation.platform_credits_gateway import PlatformCreditsGateway
from image_generation.provider_refused import ProviderRefused
from image_generation.seedream_price_estimator import SeedreamPriceEstimator


@dataclass(frozen=True)
class Backend:
    provider: str
    model: str


@dataclass
class SeedreamResult:
    media: list[GeneratedMedia]
    backend: Backend
    cost_usd: float
    notes: list[str] = field(default_factory=list)


class SeedreamImageService:
    def __init__(self, generators: dict[str, ImageGenerator], backends: list[Backend],
                 credits: PlatformCreditsGateway, estimate: SeedreamPriceEstimator) -> None:
        """:param backends: in order of preference — the first that is available makes the job."""
        self._generators = generators
        self._backends = backends
        self._credits = credits
        self._estimate = estimate

    def generate(self, prompt: str, references: list[str], aspect_ratio: str, count: int,
                 out_stem: str) -> SeedreamResult:
        left = self._credits.usd_left()
        if left is not None and left <= 0:
            raise BudgetExceeded(self._backends[0].model, self._estimate.usd(count, len(references)), 0.0)
        request = ImageRequest(model="", prompt=prompt, references=tuple(references), aspect_ratio=aspect_ratio,
                               variants=count, out_stem=out_stem, max_usd=left or 0.0)
        notes: list[str] = []
        for backend in self._backends:
            try:
                media = self._generators[backend.provider].generate(replace(request, model=backend.model))
            except (BudgetExceeded, ProviderRefused):
                raise
            except Exception as e:  # noqa: BLE001 — unavailable: the next backend makes it, and it is said
                notes.append(f"{backend.provider} was unavailable ({type(e).__name__}: {str(e)[:200]})")
                continue
            if notes:
                notes.append(f"made on {backend.provider} instead")
            cost = sum(m.cost_usd for m in media)
            try:
                self._credits.debit(cost)
            except Exception as e:  # noqa: BLE001 — the pictures are paid for and saved; the failure is said
                notes.append(f"the ${cost:.2f} charge could not be recorded: {e}")
            return SeedreamResult(media=media, backend=backend, cost_usd=cost, notes=notes)
        raise RuntimeError("no image provider could make it: " + "; ".join(notes))


__all__ = ["Backend", "SeedreamImageService", "SeedreamResult"]

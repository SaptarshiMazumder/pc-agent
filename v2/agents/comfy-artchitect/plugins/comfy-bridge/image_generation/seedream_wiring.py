"""SeedreamWiring — the image layer assembled once: providers in order, prices, the credit gateway.

The composition root for everything Seedream (comfy_bridge.register builds the run's service from
it; PipelineToolContext builds the card's price estimate from it), so the backends, their order
and their settings are decided in one place:

    1. Higgsfield seedream_v5_pro   — exact cost from its own credits; the default
    2. fal bytedance/seedream/v5/pro/edit — when Higgsfield is unavailable

Provider settings (Higgsfield's `usd_per_credit`, `client_id`, `workspace`) come from agent.toml
[plugins.comfy-bridge.providers.higgsfield]; the keys are the machine's (FAL_KEY,
HIGGSFIELD_ACCESS_TOKEN / _REFRESH_TOKEN), substituted by the host by name.
"""

from __future__ import annotations

from pathlib import Path

from image_generation.fal_image_generator import FalImageGenerator
from image_generation.fal_queue_client import FalQueueClient
from image_generation.higgsfield_api_client import HiggsfieldApiClient
from image_generation.higgsfield_image_generator import HiggsfieldImageGenerator
from image_generation.higgsfield_session import HiggsfieldSession
from image_generation.image_data_uri_encoder import ImageDataUriEncoder
from image_generation.media_downloader import MediaDownloader
from image_generation.model_spec_book import ModelSpecBook
from image_generation.platform_credits_gateway import PlatformCreditsGateway
from image_generation.price_calculator import PriceCalculator
from image_generation.provider_settings import ProviderSettings
from image_generation.run_workspace import RunWorkspace
from image_generation.seedream_image_service import Backend, SeedreamImageService
from image_generation.seedream_price_estimator import SeedreamPriceEstimator

SPECS = Path(__file__).parent / "model_specs"
BACKENDS = [Backend("higgsfield", "seedream_v5_pro"), Backend("fal", "bytedance/seedream/v5/pro/edit")]
_TIMEOUT_S = 300.0
_MAX_BYTES = 60_000_000
_INPUT_MAX_SIDE = 2048


class SeedreamWiring:
    def __init__(self, config=None) -> None:
        self._settings = ProviderSettings(config)
        self._specs = ModelSpecBook(SPECS)
        self._prices = PriceCalculator()

    def _usd_per_credit(self) -> float:
        return HiggsfieldSession(lambda: self._settings.get("higgsfield")).usd_per_credit()

    def estimator(self) -> SeedreamPriceEstimator:
        first = BACKENDS[0]
        return SeedreamPriceEstimator(self._specs, self._prices, first.provider, first.model, self._usd_per_credit)

    def service(self) -> SeedreamImageService:
        workspace = RunWorkspace()
        downloader = MediaDownloader(max_bytes=_MAX_BYTES, timeout_s=120.0)
        session = HiggsfieldSession(lambda: self._settings.get("higgsfield"))
        generators = {
            "higgsfield": HiggsfieldImageGenerator(HiggsfieldApiClient(session, workspace, poll_s=6.0), session,
                                                   self._specs, downloader, _TIMEOUT_S),
            "fal": FalImageGenerator(FalQueueClient(poll_s=4.0), self._specs,
                                     ImageDataUriEncoder(workspace, max_side=_INPUT_MAX_SIDE, quality=92),
                                     downloader, self._prices, _TIMEOUT_S),
        }
        return SeedreamImageService(generators, BACKENDS, PlatformCreditsGateway(), self.estimator())


__all__ = ["BACKENDS", "SeedreamWiring"]

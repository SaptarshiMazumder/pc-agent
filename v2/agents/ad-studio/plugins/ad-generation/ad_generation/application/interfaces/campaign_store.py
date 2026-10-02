"""Where a campaign's files live: its product photos, profile, brief, stills, clips, verdicts and
cost ledger."""

from __future__ import annotations

from typing import Protocol

from ad_generation.domain.campaign_progress import CampaignProgress
from ad_generation.domain.creative_brief import CreativeBrief
from ad_generation.domain.generated_media import GeneratedMedia
from ad_generation.domain.media_verdict import MediaVerdict
from ad_generation.domain.product_profile import ProductProfile


class CampaignStore(Protocol):
    def create(self, name: str, photos: list[str]) -> tuple[str, tuple[str, ...]]:
        """A new campaign holding copies of `photos`: (campaign id, the copies' paths)."""
        ...

    def save_profile(self, campaign_id: str, profile: ProductProfile) -> None: ...

    def profile(self, campaign_id: str) -> ProductProfile:
        """Raises KeyError when the campaign or its profile does not exist."""
        ...

    def save_brief(self, campaign_id: str, brief: CreativeBrief) -> None: ...

    def brief(self, campaign_id: str) -> CreativeBrief:
        """Raises KeyError when the campaign has no brief yet."""
        ...

    def save_progress(self, campaign_id: str, progress: CampaignProgress) -> None: ...

    def campaign_ids(self) -> list[str]:
        """Every campaign started by campaign_run (those with recipe progress)."""
        ...

    def updated(self, campaign_id: str) -> float:
        """When the campaign last changed (epoch seconds)."""
        ...

    def progress(self, campaign_id: str) -> CampaignProgress:
        """Raises KeyError when the campaign was not started by campaign_run."""
        ...

    def sheet_stem(self, campaign_id: str) -> str:
        """A fresh workspace path stem for the campaign's next shoot sheet."""
        ...

    def still_stem(self, campaign_id: str, shot_id: str) -> str:
        """A fresh workspace path stem for this shot's next batch of stills."""
        ...

    def clip_path(self, campaign_id: str, shot_id: str) -> str:
        """A fresh workspace path for this shot's next clip."""
        ...

    def record(self, campaign_id: str, media: GeneratedMedia) -> None:
        """Append to the cost ledger."""
        ...

    def record_verdict(self, campaign_id: str, verdict: MediaVerdict) -> None: ...

    def verdicts(self, campaign_id: str) -> dict[str, MediaVerdict]:
        """Every recorded verdict, by the path it judged (the latest per path)."""
        ...

    def stills_made(self, campaign_id: str, shot_id: str) -> list[list[str]]:
        """The takes of stills already on disk for this shot under the current brief, oldest
        first, each take's files together — what a step cut short left behind."""
        ...

    def clips_made(self, campaign_id: str, shot_id: str) -> list[tuple[str, str]]:
        """The clips already on disk for this shot under the current brief, oldest first, each
        with its last-frame image or ""."""
        ...

    def spent(self, campaign_id: str) -> float:
        """Total USD the ledger holds for this campaign."""
        ...

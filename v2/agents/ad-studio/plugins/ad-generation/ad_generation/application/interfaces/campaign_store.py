"""Where a campaign's files live: its product photos, profile, brief, checklist, every take of
every step, verdicts and the cost ledger."""

from __future__ import annotations

from typing import Protocol

from ad_generation.domain.campaign_checklist import CampaignChecklist
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
        """Raises KeyError when the campaign has none."""
        ...

    def save_brief(self, campaign_id: str, brief: CreativeBrief) -> None: ...

    def brief(self, campaign_id: str) -> CreativeBrief:
        """Raises KeyError when the campaign has none."""
        ...

    def save_checklist(self, campaign_id: str, checklist: CampaignChecklist) -> None: ...

    def checklist(self, campaign_id: str) -> CampaignChecklist | None:
        """The campaign's checklist; None for a campaign from before checklists (see
        legacy_progress)."""
        ...

    def legacy_progress(self, campaign_id: str) -> dict | None:
        """The gate-era progress record of a campaign made before checklists, as stored."""
        ...

    def campaign_ids(self) -> list[str]: ...

    def updated(self, campaign_id: str) -> float:
        """When anything in the campaign last changed."""
        ...

    def take_stem(self, campaign_id: str, step_id: str) -> str:
        """The next free take of a step: campaigns/<id>/steps/<step>/take-NN (no extension)."""
        ...

    def import_file(self, campaign_id: str, step_id: str, src: str) -> str:
        """A copy of the workspace file `src` as the step's next take; the copy's workspace path."""
        ...

    def record(self, campaign_id: str, media: GeneratedMedia) -> None:
        """Append a paid result to the cost ledger."""
        ...

    def record_verdict(self, campaign_id: str, verdict: MediaVerdict) -> None: ...

    def verdicts(self, campaign_id: str) -> dict[str, MediaVerdict]:
        """The latest verdict per judged path."""
        ...

    def ledger(self, campaign_id: str) -> list[GeneratedMedia]:
        """Everything the campaign paid for, oldest first."""
        ...

    def made_at(self, path: str) -> float:
        """When a workspace file was written; 0 when it is gone."""
        ...

    def spent(self, campaign_id: str) -> float: ...

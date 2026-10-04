"""Which results belong to which step: the cost ledger, grouped by the step each was made for."""

from __future__ import annotations

from ad_generation.application.interfaces.campaign_store import CampaignStore
from ad_generation.domain.campaign_checklist import CampaignChecklist
from ad_generation.domain.generated_media import GeneratedMedia
from ad_generation.domain.legacy_progress import owner_of


class StepMedia:
    def __init__(self, store: CampaignStore) -> None:
        self._store = store

    def by_step(self, campaign_id: str, checklist: CampaignChecklist) -> dict[str, list[GeneratedMedia]]:
        """step id -> its results, oldest first."""
        out: dict[str, list[GeneratedMedia]] = {s.id: [] for s in checklist.steps}
        for media in self._store.ledger(campaign_id):
            owner = owner_of(checklist, media.path, media.step)
            out.setdefault(owner, []).append(media)
        return out

    def of(self, campaign_id: str, checklist: CampaignChecklist, step_id: str) -> list[GeneratedMedia]:
        return self.by_step(campaign_id, checklist).get(step_id, [])

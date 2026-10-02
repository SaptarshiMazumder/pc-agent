"""What the window shows: the campaigns, one campaign's state, the cast — READ ONLY.

Nothing here spends, generates or moves a campaign past a gate. The window acts by sending the
user's answer as a chat message, so the daemon's approval stamps stay the only way forward; this
service only lets it SEE what the recipe has made so far.
"""

from __future__ import annotations

from ad_generation.application.interfaces.campaign_store import CampaignStore
from ad_generation.application.interfaces.cast_library import CastLibrary
from ad_generation.application.interfaces.recipe_library import RecipeLibrary
from ad_generation.domain.campaign_progress import CampaignProgress
from ad_generation.domain.campaign_report import CampaignReport


class CampaignViewService:
    def __init__(self, store: CampaignStore, recipes: RecipeLibrary, cast: CastLibrary) -> None:
        self._store = store
        self._recipes = recipes
        self._cast = cast

    def report(self, campaign_id: str, stopped: str = "") -> CampaignReport:
        return self.report_of(campaign_id, self._store.progress(campaign_id), stopped)

    def report_of(self, campaign_id: str, state: CampaignProgress, stopped: str) -> CampaignReport:
        return CampaignReport(
            campaign_id=campaign_id,
            recipe_key=state.recipe_key,
            gate=state.gate,
            brief=self._store.brief(campaign_id),
            planned=state.plan.shots,
            shots=tuple(state.outcomes[s] for s in state.plan.shots if s in state.outcomes),
            sheet=state.sheet,
            sheet_score=state.sheet_score,
            spent_usd=self._store.spent(campaign_id),
            stopped=stopped,
        )

    def detail(self, campaign_id: str) -> dict:
        """One campaign, everything the window draws: the report plus the product, the plan,
        the direction and the cast member."""
        state = self._store.progress(campaign_id)
        profile = self._store.profile(campaign_id)
        recipe = self._recipes.get(state.recipe_key)
        member = self._cast.get(state.cast_name) if state.cast_name else None
        return {
            **self.report_of(campaign_id, state, "").to_dict(),
            "recipe_title": recipe.title,
            "product": {"name": profile.name, "category": profile.category, "photos": list(profile.photos)},
            "plan": state.plan.to_dict(),
            "direction": state.direction,
            "cast": member.to_dict() if member else None,
            "session": state.session,
            "video": state.video,
            "updated": self._store.updated(campaign_id),
        }

    def campaigns(self, session: str) -> list[dict]:
        """Every campaign started by `campaign_run`, newest first; `mine` marks those this chat
        started."""
        rows = []
        for campaign_id in self._store.campaign_ids():
            state = self._store.progress(campaign_id)
            profile = self._store.profile(campaign_id)
            chosen = [o.still for o in state.outcomes.values() if o.still]
            clips = [o.clip for o in state.outcomes.values() if o.clip]
            rows.append(
                {
                    "id": campaign_id,
                    "name": profile.name,
                    "gate": state.gate,
                    "recipe": state.recipe_key,
                    "cast": state.cast_name,
                    "spent_usd": self._store.spent(campaign_id),
                    "updated": self._store.updated(campaign_id),
                    "mine": bool(session) and state.session == session,
                    "cover": chosen[0] if chosen else (profile.photos[0] if profile.photos else ""),
                    "stills": len(chosen),
                    "clips": len(clips),
                }
            )
        return sorted(rows, key=lambda r: r["updated"], reverse=True)

    def cast(self) -> list[dict]:
        return [m.to_dict() for m in self._cast.all()]

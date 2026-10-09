"""What the window shows: the campaigns, one campaign's steps with every result of each, the cast —
READ ONLY. Nothing here spends or generates.
"""

from __future__ import annotations

from ad_generation.application.campaign_checklist_loader import CampaignChecklistLoader
from ad_generation.application.interfaces.campaign_store import CampaignStore
from ad_generation.application.interfaces.cast_library import CastLibrary
from ad_generation.application.interfaces.poster_text_store import PosterTextStore
from ad_generation.application.interfaces.recipe_library import RecipeLibrary
from ad_generation.application.step_defaults import StepDefaults
from ad_generation.application.step_media import StepMedia
from ad_generation.domain.campaign_checklist import CampaignChecklist
from ad_generation.domain.campaign_step import CampaignStep
from ad_generation.domain.generated_media import GeneratedMedia
from ad_generation.domain.media_verdict import MediaVerdict


class CampaignViewService:
    def __init__(
        self,
        store: CampaignStore,
        recipes: RecipeLibrary,
        cast: CastLibrary,
        loader: CampaignChecklistLoader,
        defaults: StepDefaults,
        media: StepMedia,
        texts: PosterTextStore,
    ) -> None:
        self._texts = texts
        self._store = store
        self._recipes = recipes
        self._cast = cast
        self._loader = loader
        self._defaults = defaults
        self._media = media

    def detail(self, campaign_id: str) -> dict:
        """One campaign: its steps, each with its results (newest first), its pick, and what a
        run without changes would send (its default prompt and references)."""
        checklist = self._loader.load(campaign_id)
        profile = self._store.profile(campaign_id)
        recipe = self._recipes.get(checklist.recipe_key)
        verdicts = self._store.verdicts(campaign_id)
        by_step = self._media.by_step(campaign_id, checklist)
        member = self._cast.get(checklist.cast_name) if checklist.cast_name else None
        current = checklist.current()
        try:
            brief = self._store.brief(campaign_id).to_dict()
        except KeyError:
            brief = None
        return {
            "campaign_id": campaign_id,
            "recipe_key": checklist.recipe_key,
            "recipe_title": recipe.title,
            "recipe_name": recipe.name,
            "product": {"name": profile.name, "category": profile.category, "photos": list(profile.photos)},
            "cast": member.to_dict() if member else None,
            "brief": brief,
            "direction": checklist.direction,
            "session": checklist.session,
            "budget_usd": checklist.budget_usd,
            "spent_usd": self._store.spent(campaign_id),
            "updated": self._store.updated(campaign_id),
            "current": current.id if current else "",
            "approval": checklist.approval,
            "steps": [
                self._step_view(campaign_id, checklist, s, by_step.get(s.id, []), verdicts) for s in checklist.steps
            ],
        }

    def campaigns(self, session: str) -> list[dict]:
        """Every campaign, newest first; `mine` marks those this chat started."""
        rows = []
        for campaign_id in self._store.campaign_ids():
            checklist = self._loader.load(campaign_id)
            profile = self._store.profile(campaign_id)
            ledger = self._store.ledger(campaign_id)
            images = checklist.first("images")
            current = checklist.current()
            rows.append(
                {
                    "id": campaign_id,
                    "name": profile.name,
                    "step": current.title if current else "done",
                    "recipe": checklist.recipe_key,
                    "cast": checklist.cast_name,
                    "spent_usd": sum(m.cost_usd for m in ledger),
                    "updated": self._store.updated(campaign_id),
                    "mine": bool(session) and checklist.session == session,
                    "cover": (images.pick if images and images.pick else (profile.photos[0] if profile.photos else "")),
                    "stills": sum(1 for m in ledger if m.kind == "image"),
                    "clips": sum(1 for m in ledger if m.kind == "video"),
                }
            )
        return sorted(rows, key=lambda r: r["updated"], reverse=True)

    def generations(self, campaign_id: str) -> list[dict]:
        """Everything the campaign paid for, newest first, with the step it belongs to."""
        checklist = self._loader.load(campaign_id)
        verdicts = self._store.verdicts(campaign_id)
        picks = {s.pick for s in checklist.steps if s.pick}
        titles = {s.id: s.title for s in checklist.steps}
        rows = []
        for step_id, results in self._media.by_step(campaign_id, checklist).items():
            for media in results:
                row = self._result(media, verdicts.get(media.path))
                row.update({"step": step_id, "step_title": titles.get(step_id, step_id), "in_use": media.path in picks})
                rows.append(row)
        return sorted(rows, key=lambda r: r["made_at"], reverse=True)

    def cast(self) -> list[dict]:
        return [m.to_dict() for m in self._cast.all()]

    def recipes(self) -> list[dict]:
        """Every recipe, for the window to start an ad from: what it is for, its steps in order,
        and whether it casts a model (then the window offers the cast next)."""
        return [
            {
                "key": r.key,
                "name": r.name,
                "title": r.title,
                "aspect_ratio": r.aspect_ratio,
                "budget_usd": r.budget_usd,
                "covers": r.covers,
                "steps": [{"id": s.id, "title": s.title, "action": s.action} for s in r.steps],
                "needs_cast": r.needs_cast(),
                "variants": r.variants,
                "scene_options": list(r.scene_options),
                "brief": r.brief,
                "start_text": r.start_text,
            }
            for r in sorted(self._recipes.all(), key=lambda r: r.key)
        ]

    # ---- pieces ------------------------------------------------------------------------------

    def _step_view(
        self,
        campaign_id: str,
        checklist: CampaignChecklist,
        step: CampaignStep,
        results: list[GeneratedMedia],
        verdicts: dict[str, MediaVerdict],
    ) -> dict:
        rows = [self._result(m, verdicts.get(m.path)) for m in results]
        rows.sort(key=lambda r: r["made_at"], reverse=True)
        defaults = {"prompt": "", "references": [], "seconds": 0}
        if step.action in ("images", "video"):
            defaults = {
                "prompt": self._defaults.prompt(campaign_id, step),
                "references": list(self._defaults.references(campaign_id, checklist, step)),
                "seconds": self._defaults.seconds(campaign_id, step) if step.action == "video" else 0,
            }
        if step.action == "product_sheet":
            defaults = {**defaults, "panels": self._defaults.panels(checklist) if step.pick else []}
        stale = False
        if step.action == "video" and step.pick:
            source_pick = checklist.step(step.source).pick
            made_from = next((r["first_frame"] for r in rows if r["path"] == step.pick), "")
            stale = bool(source_pick and made_from and made_from != source_pick)
        return {**step.to_dict(), "defaults": defaults, "results": rows, "stale": stale}

    def _result(self, media: GeneratedMedia, verdict: MediaVerdict | None) -> dict:
        return {
            "path": media.path,
            "kind": media.kind,
            "provider": media.provider,
            "model": media.model,
            "cost_usd": media.cost_usd,
            "credits": media.detail.get("credits"),
            "made_at": self._store.made_at(media.path),
            "score": verdict.score if verdict else None,
            "passed": verdict.passed if verdict else None,
            "text_exact": verdict.text_exact if verdict else None,
            "problems": list(verdict.problems) if verdict else [],
            "first_frame": str(media.detail.get("first_frame") or ""),
            "fix_of": str(media.detail.get("fix_of") or ""),
            "from_clip": str(media.detail.get("from_clip") or ""),
            "uploaded": media.provider == "upload",
            "editable": media.kind == "image" and self._texts.load(media.path) is not None,
        }

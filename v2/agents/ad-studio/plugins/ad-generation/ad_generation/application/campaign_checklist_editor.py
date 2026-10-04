"""Changes to a campaign's checklist that cost nothing and generate nothing: pick a step's result,
bring in the user's own image as a result, add a step of the user's own, change or skip a step.
Instant — the window calls `pick` and `import_image` directly.
"""

from __future__ import annotations

from ad_generation.application.campaign_checklist_loader import CampaignChecklistLoader
from ad_generation.application.interfaces.campaign_store import CampaignStore
from ad_generation.application.interfaces.recipe_library import RecipeLibrary
from ad_generation.application.step_media import StepMedia
from ad_generation.domain.campaign_step import CampaignStep
from ad_generation.domain.generated_media import GeneratedMedia

# The images a user can bring in as a step's result.
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
# What `update` may change; the results and the pick are changed by running and picking.
_EDITABLE = ("title", "prompt", "references", "scene", "source", "status", "cast", "shows_product", "count", "text")


class CampaignChecklistEditor:
    def __init__(
        self, store: CampaignStore, recipes: RecipeLibrary, loader: CampaignChecklistLoader, media: StepMedia
    ) -> None:
        self._store = store
        self._recipes = recipes
        self._loader = loader
        self._media = media

    def pick(self, campaign_id: str, step_id: str, path: str) -> CampaignStep:
        """The result the step carries forward — one of ITS results."""
        checklist = self._loader.load(campaign_id)
        step = checklist.step(step_id)
        mine = [m.path for m in self._media.of(campaign_id, checklist, step_id)]
        if path not in mine:
            raise ValueError(f"'{path}' is not a result of step {step_id} ({len(mine)} results)")
        step = step.with_(pick=path, status="done")
        checklist.put(step)
        self._store.save_checklist(campaign_id, checklist)
        return step

    def import_image(self, campaign_id: str, step_id: str, path: str) -> GeneratedMedia:
        """The user's own image, uploaded, as a result of an image step — it is picked, fixed, used
        as a reference or a clip's first frame like any generated one. Free; not checked (it is
        theirs). The step's first result becomes its pick, as with a run."""
        checklist = self._loader.load(campaign_id)
        step = checklist.step(step_id)
        if step.action not in ("images", "sheet", "product_sheet"):
            raise ValueError(f"step {step_id} makes {step.action}; an image goes into an images step")
        if not path.lower().endswith(_IMAGE_SUFFIXES):
            raise ValueError(f"{path}: not an image this can use (png, jpg, webp)")
        rel = self._store.import_file(campaign_id, step_id, path)
        media = GeneratedMedia(
            path=rel,
            kind="image",
            provider="upload",
            model="your image",
            cost_usd=0.0,
            cost_basis="exact",
            detail={"uploaded_from": path.replace("\\", "/").rsplit("/", 1)[-1]},
            step=step_id,
        )
        self._store.record(campaign_id, media)
        checklist.put(step.with_(pick=step.pick or rel, status="done"))
        self._store.save_checklist(campaign_id, checklist)
        return media

    def add(self, campaign_id: str, step: CampaignStep, after: str = "") -> CampaignStep:
        """A step of the user's own ("a product-only tabletop still"); it runs like any other."""
        checklist = self._loader.load(campaign_id)
        if not step.id:
            step = step.with_(id=checklist.new_id(step.title))
        if step.action == "images" and not step.count:
            step = step.with_(count=self._recipes.get(checklist.recipe_key).variants)
        checklist.add(step, after)
        self._store.save_checklist(campaign_id, checklist)
        return step

    def update(self, campaign_id: str, step_id: str, changes: dict) -> CampaignStep:
        """Change a step in place — its title, prompt, references, scene, source, status (skip it,
        or open it again). Its results and pick stay."""
        unknown = [k for k in changes if k not in _EDITABLE]
        if unknown:
            raise ValueError(f"a step's {', '.join(unknown)} cannot be changed here (only {', '.join(_EDITABLE)})")
        checklist = self._loader.load(campaign_id)
        if changes.get("source"):
            checklist.step(changes["source"])  # raises, naming the steps
        if "references" in changes:
            changes = {**changes, "references": tuple(changes["references"])}
        step = checklist.step(step_id).with_(**changes)
        checklist.put(step)
        self._store.save_checklist(campaign_id, checklist)
        return step

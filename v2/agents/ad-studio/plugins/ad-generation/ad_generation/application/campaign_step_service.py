"""A campaign's steps: start one from its recipe, and run any step — again and again. (Picking,
adding and changing steps cost nothing and live in CampaignChecklistEditor.)

NOTHING IS LOCKED. There is no gate pointer: any step runs at any time, every run ADDS results to
its step, and later steps are never touched by it — a clip made from a still the user has since
replaced is simply shown as made from an older still, for the user to remake or keep. Following
the recipe is running its steps in order; deviating from it is running them in any other order,
or adding steps.

A step's first results set its pick (the best-scoring one) so the recipe can be followed by
just continuing; after that the pick changes only when the user picks. The checker's verdicts are
advice shown beside each result — they never pick, block or retry anything.
"""

from __future__ import annotations

from ad_generation.application.animation_service import AnimationService
from ad_generation.application.brief_service import BriefService
from ad_generation.application.campaign_checklist_loader import CampaignChecklistLoader
from ad_generation.application.interfaces.campaign_store import CampaignStore
from ad_generation.application.interfaces.clip_frames import ClipFrames
from ad_generation.application.interfaces.progress_reporter import ProgressReporter
from ad_generation.application.interfaces.recipe_library import RecipeLibrary
from ad_generation.application.keyframe_service import KeyframeService
from ad_generation.application.poster_brief_service import PosterBriefService
from ad_generation.application.poster_text_service import PosterTextService
from ad_generation.application.product_sheet_service import ProductSheetService
from ad_generation.application.product_analysis_service import ProductAnalysisService
from ad_generation.application.quality_check_service import QualityCheckService
from ad_generation.application.shoot_sheet_service import ShootSheetService
from ad_generation.application.step_defaults import StepDefaults
from ad_generation.domain.campaign_checklist import CampaignChecklist
from ad_generation.domain.creative_direction import CreativeDirection
from ad_generation.domain.generated_media import GeneratedMedia
from ad_generation.domain.generation_backends import GenerationBackends
from ad_generation.domain.media_verdict import MediaVerdict
from ad_generation.domain.step_run import StepRun


class CampaignStepService:
    def __init__(
        self,
        analysis: ProductAnalysisService,
        briefs: BriefService,
        posters: PosterBriefService,
        sheets: ShootSheetService,
        product_sheets: ProductSheetService,
        keyframes: KeyframeService,
        animation: AnimationService,
        checks: QualityCheckService,
        frames: ClipFrames,
        store: CampaignStore,
        recipes: RecipeLibrary,
        loader: CampaignChecklistLoader,
        defaults: StepDefaults,
        poster_text: PosterTextService,
        progress: ProgressReporter,
    ) -> None:
        self._analysis = analysis
        self._briefs = briefs
        self._posters = posters
        self._sheets = sheets
        self._product_sheets = product_sheets
        self._keyframes = keyframes
        self._animation = animation
        self._checks = checks
        self._frames = frames
        self._store = store
        self._recipes = recipes
        self._loader = loader
        self._defaults = defaults
        self._poster_text = poster_text
        self._progress = progress

    # ---- a new campaign ----------------------------------------------------------------------

    def start(
        self,
        name: str,
        photos: list[str],
        cast_name: str,
        direction: CreativeDirection,
        recipe_key: str,
        session: str,
        approval: str = "ask",
    ) -> tuple[str, CampaignChecklist]:
        """Read the product, copy its recipe's steps, and run the first one (the brief)."""
        self._progress.say(f"reading {name}")
        campaign_id, profile = self._analysis.analyze(name, photos, direction.notes)
        recipe = self._recipes.get(recipe_key or profile.recipe)
        self._progress.say(f"{campaign_id}: {profile.category} -> recipe {recipe.key}; writing the brief")
        brief = self._writer(recipe).write_for_recipe(campaign_id, recipe, cast_name, direction)
        cast = next((s.cast[0] for s in brief.shots if s.cast), "")
        checklist = recipe.new_checklist(cast, direction.given(), session, approval)
        first = checklist.first("brief")
        if first:
            checklist.put(first.with_(status="done"))
        self._store.save_checklist(campaign_id, checklist)
        return campaign_id, checklist

    # ---- any step, any time ------------------------------------------------------------------

    def run(
        self, campaign_id: str, step_id: str, run: StepRun, backends: GenerationBackends, budget_usd: float = 0.0
    ) -> tuple[list[GeneratedMedia], list[MediaVerdict]]:
        """Run one step once more; its results are added to it. -> (results, their verdicts)."""
        checklist = self._loader.load(campaign_id)
        if budget_usd:
            checklist.budget_usd = budget_usd
        step = checklist.step(step_id)
        if step.action == "brief":
            made, verdicts = self._brief(campaign_id, checklist, run), []
        elif step.action == "sheet":
            made, verdicts = self._sheet(campaign_id, checklist, step, run, backends)
        elif step.action == "product_sheet":
            made, verdicts = self._product_sheet(campaign_id, checklist, step, run, backends)
        elif step.action == "images":
            made, verdicts = self._images(campaign_id, checklist, step, run, backends)
        else:
            made, verdicts = self._video(campaign_id, checklist, step, run, backends)
        step = checklist.step(step_id)
        if not step.pick and made:
            step = step.with_(pick=self._best(made, verdicts))
        checklist.put(step.with_(status="done"))
        self._store.save_checklist(campaign_id, checklist)
        return made, verdicts

    # ---- the actions -------------------------------------------------------------------------

    def _brief(self, campaign_id: str, checklist: CampaignChecklist, run: StepRun) -> list[GeneratedMedia]:
        recipe = self._recipes.get(checklist.recipe_key)
        given = {**checklist.direction, **run.direction}
        if run.change.strip():
            given["notes"] = "; ".join(n for n in (given.get("notes", ""), run.change.strip()) if n)
        self._progress.say("rewriting the brief" + (" with the changes" if run.change.strip() else ""))
        self._writer(recipe).write_for_recipe(campaign_id, recipe, checklist.cast_name, CreativeDirection(**given))
        checklist.direction = given  # the next rewrite builds on this one's change
        return []

    def _sheet(self, campaign_id, checklist, step, run, backends) -> tuple[list[GeneratedMedia], list[MediaVerdict]]:
        if not checklist.cast_name:
            raise ValueError("a shoot sheet needs a cast member, and this campaign has none")
        provider, model = self._choice(run.model or step.model, backends.image)
        self._progress.say(f"making the shoot sheet on {provider}/{model}")
        sheet, verdict = self._sheets.make(
            campaign_id, step.id, checklist.cast_name, run.change, provider, model, max_usd=self._left(campaign_id, checklist)
        )
        checklist.put(step.with_(model=f"{provider}/{model}"))
        return [sheet], [verdict]

    def _product_sheet(self, campaign_id, checklist, step, run, backends) -> tuple[list[GeneratedMedia], list[MediaVerdict]]:
        provider, model = self._choice(run.model or step.model, backends.image)
        self._progress.say(f"making the product sheet on {provider}/{model}")
        sheet, verdict = self._product_sheets.make(
            campaign_id, step.id, run.change, provider, model, max_usd=self._left(campaign_id, checklist)
        )
        checklist.put(step.with_(model=f"{provider}/{model}"))
        return [sheet], [verdict]

    def _images(self, campaign_id, checklist, step, run, backends) -> tuple[list[GeneratedMedia], list[MediaVerdict]]:
        provider, model = self._choice(run.model or step.model, backends.image)
        count = run.count or step.count or 1
        self._progress.say(f"{step.title}: making {count} image(s) on {provider}/{model}")
        if step.text == "overlay" and not (step.scene and self._defaults.copy_lines(campaign_id, step)):
            raise ValueError(f"step {step.id} sets a text ad's words in real fonts, and it makes no text-ad design")
        made = self._keyframes.generate(
            campaign_id, checklist, step, run, count, provider, model, max_usd=self._left(campaign_id, checklist)
        )
        if step.text == "overlay":
            self._progress.say(f"{step.title}: setting the words in real fonts")
            for m in made:
                self._poster_text.typeset(campaign_id, m, step.scene)
        verdicts = [self._check(campaign_id, checklist, step, m.path, "") for m in made]
        checklist.put(step.with_(model=f"{provider}/{model}", count=count))
        return made, verdicts

    def _video(self, campaign_id, checklist, step, run, backends) -> tuple[list[GeneratedMedia], list[MediaVerdict]]:
        first_frame = run.first_frame or checklist.step(step.source).pick
        if not first_frame:
            raise ValueError(f"step {step.id} starts from the image picked in '{step.source}', and none is picked yet")
        provider, model = self._choice(run.model or step.model, backends.video)
        seconds = run.seconds or self._defaults.seconds(campaign_id, step)
        resolution = run.resolution or step.resolution or self._recipes.get(checklist.recipe_key).resolution
        self._progress.say(f"{step.title}: a {seconds} s clip at {resolution} on {provider}/{model}")
        clip = self._animation.animate(
            campaign_id, checklist, step, run, first_frame, seconds, resolution, backends.audio, provider, model,
            max_usd=self._left(campaign_id, checklist),
        )
        last = clip.last_frame or self._frames.last_frame(clip.path)
        verdict = self._check(campaign_id, checklist, step, clip.path, last)
        checklist.put(step.with_(model=f"{provider}/{model}", seconds=seconds, resolution=resolution))
        return [clip], [verdict]

    # ---- shared ------------------------------------------------------------------------------

    def _check(self, campaign_id, checklist, step, path: str, last_frame: str) -> MediaVerdict:
        cast = (checklist.cast_name,) if step.cast and checklist.cast_name else ()
        # Words set by code are exact by construction: only model-drawn words are read back.
        copy = () if step.text == "overlay" else self._defaults.copy_lines(campaign_id, step)
        verdict = self._checks.check(campaign_id, path, last_frame, step.title, cast, step.shows_product, copy)
        self._progress.say(f"{path.rsplit('/', 1)[-1]}: {verdict.score}/10" + ("" if verdict.passed else " — " + "; ".join(verdict.problems)))
        return verdict

    def _writer(self, recipe) -> BriefService | PosterBriefService:
        """A text-ad recipe's brief is its copy and design; any other's, its look and shots."""
        return self._posters if recipe.brief == "poster" else self._briefs

    @staticmethod
    def _best(made: list[GeneratedMedia], verdicts: list[MediaVerdict]) -> str:
        if not verdicts:
            return made[0].path
        best = max(verdicts, key=lambda v: (v.passed, v.score))
        return best.path

    @staticmethod
    def _choice(chosen: str, default: tuple[str, str]) -> tuple[str, str]:
        return tuple(chosen.split("/", 1)) if chosen else default  # type: ignore[return-value]

    def _left(self, campaign_id: str, checklist: CampaignChecklist) -> float:
        """What the campaign has left to spend — or stop, saying how to go on."""
        spent = self._store.spent(campaign_id)
        if spent >= checklist.budget_usd:
            raise ValueError(
                f"the campaign's budget is spent (${spent:.2f} of ${checklist.budget_usd:.2f}); "
                "raise it with budget_usd to go on"
            )
        return checklist.budget_usd - spent

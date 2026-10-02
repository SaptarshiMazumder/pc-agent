"""The recipe, one step at a time — the SAME steps for every product of a kind.

    read the product -> its recipe -> the brief            ── GATE brief
    per shot: stills -> check each -> retry with fixes     ── GATE stills
    per animated shot: clip from the chosen still -> check ── GATE clips -> done

After each step the campaign STOPS at its gate (when the recipe lists it) and waits: the user
looks at the results and approves, picks a different still, or asks for a redo with changes. It
moves on only when the user has ANSWERED an ask shown after the results were made — the daemon's
stamps say so, not the agent (ApprovalLedger). A redo re-runs the gate's own step and stops at
the same gate again.

A STEP CUT SHORT RESUMES WHERE IT STOPPED. A daemon restart mid-step leaves stills and clips on
disk that were paid for; the step judges those first — reusing the verdicts on record, checking
only what was never checked — and generates only if none of them pass. A redo never reuses:
the user asked for new ones.

The steps are decided here, in code, not by the model. The model's judgement is used where it
belongs: writing each scene, and judging each result.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Callable

from ad_generation.application.animation_service import AnimationService
from ad_generation.application.brief_service import BriefService
from ad_generation.application.interfaces.approval_ledger import ApprovalLedger
from ad_generation.application.interfaces.campaign_store import CampaignStore
from ad_generation.application.interfaces.progress_reporter import ProgressReporter
from ad_generation.application.interfaces.provider_refused import ProviderRefused
from ad_generation.application.interfaces.recipe_library import RecipeLibrary
from ad_generation.application.keyframe_service import KeyframeService
from ad_generation.application.product_analysis_service import ProductAnalysisService
from ad_generation.application.quality_check_service import QualityCheckService
from ad_generation.application.shoot_sheet_service import ShootSheetService
from ad_generation.domain.campaign_progress import BRIEF, CLIPS, DONE, SHEET, STILLS, CampaignProgress
from ad_generation.domain.campaign_report import CampaignReport
from ad_generation.domain.creative_direction import CreativeDirection
from ad_generation.domain.generation_backends import GenerationBackends
from ad_generation.domain.media_verdict import MediaVerdict
from ad_generation.domain.recipe import Recipe
from ad_generation.domain.run_options import RunOptions
from ad_generation.domain.shot import Shot
from ad_generation.domain.shot_outcome import ShotOutcome

_NEXT = {BRIEF: STILLS, SHEET: STILLS, STILLS: CLIPS, CLIPS: DONE}


class _BudgetReached(Exception):
    pass


class CampaignRunService:
    def __init__(
        self,
        analysis: ProductAnalysisService,
        briefs: BriefService,
        keyframes: KeyframeService,
        animation: AnimationService,
        sheets: ShootSheetService,
        checks: QualityCheckService,
        store: CampaignStore,
        recipes: RecipeLibrary,
        approvals: ApprovalLedger,
        progress: ProgressReporter,
        clock: Callable[[], float],
    ) -> None:
        self._analysis = analysis
        self._briefs = briefs
        self._keyframes = keyframes
        self._animation = animation
        self._sheets = sheets
        self._checks = checks
        self._store = store
        self._recipes = recipes
        self._approvals = approvals
        self._progress = progress
        self._clock = clock

    # ---- the two entry points ----------------------------------------------------------------

    def start(
        self,
        name: str,
        photos: list[str],
        cast_name: str,
        direction: CreativeDirection,
        recipe_key: str,
        backends: GenerationBackends,
        options: RunOptions,
    ) -> CampaignReport:
        self._progress.say(f"reading {name}")
        campaign_id, profile = self._analysis.analyze(name, photos, direction.notes)
        recipe = self._recipes.get(recipe_key or profile.recipe)
        plan = options.resolved(recipe)
        self._progress.say(
            f"{campaign_id}: {profile.category} -> recipe {recipe.key}; shots {', '.join(plan.shots)}, "
            f"clips {', '.join(sorted(plan.animate)) or 'none'} at {plan.resolution}; writing the brief"
        )
        brief = self._briefs.write_for_recipe(campaign_id, recipe, cast_name, direction)
        state = CampaignProgress(
            recipe_key=recipe.key,
            plan=plan,
            direction=direction.given(),
            cast_name=next((s.cast[0] for s in brief.shots if s.cast), ""),
            gate=BRIEF,
            gate_reached_at=self._clock(),
        )
        return self._settle(campaign_id, recipe, state, backends)

    def resume(
        self,
        campaign_id: str,
        picks: dict[str, str],
        redo: dict[str, str],
        budget_usd: float,
        backends: GenerationBackends,
    ) -> CampaignReport:
        """Past the gate the campaign waits at — only once the user has answered for it.
        `picks` chooses a still per shot (stills gate); `redo` re-runs the gate's step for the
        named shots ("brief" at the brief gate) with the user's changes, and stays at the gate."""
        state = self._store.progress(campaign_id)
        if state.gate == DONE:
            raise ValueError(f"{campaign_id} is finished; use the single-step tools for further changes")
        refusal = self._approvals.latest().refusal(state.gate, state.gate_reached_at)
        if refusal:
            raise PermissionError(f"{campaign_id} waits at the {state.gate} gate: {refusal}")
        recipe = self._recipes.get(state.recipe_key)
        if budget_usd:
            state.plan = replace(state.plan, budget_usd=budget_usd)
        self._pick(state, picks)
        if redo:
            return self._redo(campaign_id, recipe, state, redo, backends)
        try:
            self._advance(campaign_id, recipe, state, backends)
        except _BudgetReached as e:
            return self._report(campaign_id, state, str(e))
        return self._settle(campaign_id, recipe, state, backends)

    def status(self, campaign_id: str) -> CampaignReport:
        """Where the campaign stands — the gate it waits at and what that gate holds. What a
        conversation that did not start the campaign shows before it asks."""
        state = self._store.progress(campaign_id)
        return self._report(campaign_id, state, "")

    # ---- moving between gates ----------------------------------------------------------------

    def _settle(
        self, campaign_id: str, recipe: Recipe, state: CampaignProgress, backends: GenerationBackends
    ) -> CampaignReport:
        """Run on through every gate this run does not stop at; save; report. A budget stop
        holds the campaign at the gate it reached."""
        try:
            while state.gate != DONE and state.gate not in state.plan.gates:
                self._advance(campaign_id, recipe, state, backends)
        except _BudgetReached as e:
            return self._report(campaign_id, state, str(e))
        self._store.save_progress(campaign_id, state)
        return self._report(campaign_id, state, "")

    def _advance(
        self, campaign_id: str, recipe: Recipe, state: CampaignProgress, backends: GenerationBackends
    ) -> None:
        """Run the step after the current gate; the campaign then waits at that step's gate.
        A budget stop leaves it at the new gate with what was made so far."""
        step = _NEXT[state.gate]
        if state.gate == BRIEF and recipe.shoot_sheet:
            step = SHEET
        brief = self._store.brief(campaign_id)
        if step == CLIPS and not any(state.outcomes[s].still for s in state.plan.animate if s in state.outcomes):
            step = DONE  # nothing to animate: a clips gate would show nothing
        state.gate = step
        try:
            if step == SHEET:
                self._sheet(campaign_id, state, backends, "")
            elif step == STILLS:
                for shot in (s for s in brief.shots if s.id in state.plan.shots):
                    state.outcomes[shot.id] = ShotOutcome(shot.id, "not reached")
                for shot in (s for s in brief.shots if s.id in state.plan.shots):
                    state.outcomes[shot.id] = self._stills(
                        campaign_id, recipe, state, shot, backends, "", self._store.stills_made(campaign_id, shot.id)
                    )
            elif step == CLIPS:
                for shot in (s for s in brief.shots if s.id in state.plan.animate):
                    if state.outcomes[shot.id].still:
                        state.outcomes[shot.id] = self._clip(
                            campaign_id, recipe, state, shot, backends, "", self._store.clips_made(campaign_id, shot.id)
                        )
        finally:
            state.gate_reached_at = self._clock()
            self._store.save_progress(campaign_id, state)

    def _redo(
        self, campaign_id: str, recipe: Recipe, state: CampaignProgress, redo: dict[str, str], backends
    ) -> CampaignReport:
        allowed = {
            BRIEF: ("brief",), SHEET: ("sheet",), STILLS: state.plan.shots, CLIPS: tuple(sorted(state.plan.animate))
        }[state.gate]
        unknown = [k for k in redo if k not in allowed]
        if unknown:
            raise ValueError(
                f"at the {state.gate} gate a redo names {', '.join(allowed)}; not {', '.join(unknown)}"
            )
        stopped = ""
        try:
            if state.gate == BRIEF:
                given = dict(state.direction)
                given["notes"] = "; ".join(n for n in (given.get("notes", ""), redo["brief"].strip()) if n)
                self._progress.say("rewriting the brief with the changes")
                self._briefs.write_for_recipe(campaign_id, recipe, state.cast_name, CreativeDirection(**given))
                state.direction = given  # a later redo builds on this one's change
            elif state.gate == SHEET:
                self._sheet(campaign_id, state, backends, redo["sheet"])
            else:
                brief = self._store.brief(campaign_id)
                for shot in (s for s in brief.shots if s.id in redo):
                    if state.gate == STILLS:
                        state.outcomes[shot.id] = self._stills(campaign_id, recipe, state, shot, backends, redo[shot.id], [])
                    elif state.outcomes[shot.id].still:
                        state.outcomes[shot.id] = self._clip(campaign_id, recipe, state, shot, backends, redo[shot.id], [])
                    else:
                        raise ValueError(f"{shot.id} has no still to animate; redo its stills first")
        except _BudgetReached as e:
            stopped = str(e)
        state.gate_reached_at = self._clock()
        self._store.save_progress(campaign_id, state)
        return self._report(campaign_id, state, stopped)

    def _pick(self, state: CampaignProgress, picks: dict[str, str]) -> None:
        if not picks:
            return
        if state.gate != STILLS:
            raise ValueError(f"stills are picked at the stills gate; this campaign waits at {state.gate}")
        for shot_id, path in picks.items():
            if shot_id not in state.outcomes:
                raise ValueError(f"this run makes no shot '{shot_id}' (shots: {', '.join(state.outcomes)})")
            state.outcomes[shot_id] = state.outcomes[shot_id].picked(path)

    # ---- the steps ---------------------------------------------------------------------------

    def _sheet(self, campaign_id: str, state: CampaignProgress, backends, correction: str) -> None:
        """The shoot sheet; `correction` is the user's change on a redo. A sheet that is not the
        cast member is kept on disk but not used — the gate shows why, and the user redoes it."""
        self._guard(campaign_id, state.plan.budget_usd)
        self._progress.say("making the shoot sheet: the cast member in this ad's outfit and light")
        sheet, verdict = self._sheets.make(campaign_id, state.cast_name, correction, *backends.image)
        if verdict.identity_kept:
            state.sheet, state.sheet_score = sheet.path, verdict.score
            self._progress.say(f"shoot sheet ready ({verdict.score}/10)")
        else:
            state.sheet, state.sheet_score = "", 0
            self._progress.say("shoot sheet is not the cast member — " + "; ".join(verdict.problems))

    def _stills(
        self,
        campaign_id: str,
        recipe: Recipe,
        state: CampaignProgress,
        shot: Shot,
        backends,
        correction: str,
        made: list[list[str]],
    ) -> ShotOutcome:
        """Stills until some pass, retrying with the check's fixes. `made` are the takes already
        on disk from a step cut short: judged first, before anything is paid for, and counted as
        attempts spent. `correction` is the user's change on a redo, carried into the first
        attempt."""
        pending = list(made)
        attempts = 0
        best_failed = None
        while pending or attempts < recipe.max_still_attempts:
            attempts += 1
            if pending:
                paths = pending.pop(0)
                self._progress.say(f"{shot.id}: judging {len(paths)} still(s) already made (attempt {attempts})")
            else:
                self._guard(campaign_id, state.plan.budget_usd)
                self._progress.say(f"{shot.id}: stills (attempt {attempts}/{recipe.max_still_attempts})")
                try:
                    made_now = self._keyframes.generate(
                        campaign_id, shot.id, state.plan.variants, correction, *backends.image, shoot_sheet=state.sheet
                    )
                except ProviderRefused as e:
                    # The same input would be refused again: no retry. The gate shows the reason.
                    self._progress.say(f"{shot.id}: {e}")
                    return ShotOutcome(shot.id, "still failed", problems=(str(e),))
                paths = [m.path for m in made_now]
            verdicts = self._judge(campaign_id, shot, paths)
            passing = sorted((v for v in verdicts if v.passed), key=lambda v: v.score, reverse=True)
            if passing:
                self._progress.say(f"{shot.id}: {len(passing)} still(s) passed (best {passing[0].score}/10)")
                return ShotOutcome(
                    shot.id, "stills ready", stills={v.path: v.score for v in passing}, still=passing[0].path
                )
            best_failed = max(verdicts, key=lambda v: v.score)
            correction = "; ".join(best_failed.problems)
            self._progress.say(f"{shot.id}: no still passed — {correction}")
        return ShotOutcome(shot.id, "still failed", problems=best_failed.problems if best_failed else ())

    def _clip(
        self,
        campaign_id: str,
        recipe: Recipe,
        state: CampaignProgress,
        shot: Shot,
        backends,
        correction: str,
        made: list[tuple[str, str]],
    ) -> ShotOutcome:
        """The clip from the chosen still, retrying with the check's fixes. `made` are the clips
        already on disk from a step cut short, each with its last frame — judged first."""
        outcome = state.outcomes[shot.id]
        pending = list(made)
        attempts = 0
        clip_path = ""
        clip_problems: tuple[str, ...] = ()
        while pending or attempts < recipe.max_clip_attempts:
            attempts += 1
            if pending:
                clip_path, last_frame = pending.pop(0)
                self._progress.say(f"{shot.id}: judging the clip already made (attempt {attempts})")
            else:
                self._guard(campaign_id, state.plan.budget_usd)
                self._progress.say(
                    f"{shot.id}: animating at {state.plan.resolution} (attempt {attempts}/{recipe.max_clip_attempts})"
                )
                try:
                    clip = self._animation.animate(
                        campaign_id, shot.id, outcome.still, state.plan.resolution, backends.audio, correction,
                        *backends.video, companions=self._companions(state, shot.id),
                    )
                except ProviderRefused as e:
                    # The same still would be refused again: no retry. The gate shows the reason,
                    # and the user picks another still, changes the shot or switches the model.
                    self._progress.say(f"{shot.id}: {e}")
                    return replace(outcome, status="clip failed", clip="", clip_check="refused", problems=(str(e),))
                clip_path, last_frame = clip.path, clip.last_frame
            if not last_frame:
                # The provider returned no last frame, so the clip cannot be judged here; it is
                # delivered marked unchecked for the user's eye rather than passed unseen.
                return replace(outcome, status="clip ready", clip=clip_path, clip_check="unchecked", problems=())
            verdict = self._judge(campaign_id, shot, [clip_path], last_frame)[0]
            if verdict.passed:
                return replace(outcome, status="clip ready", clip=clip_path, clip_check="passed", problems=())
            clip_problems = verdict.problems
            correction = "; ".join(verdict.problems)
            self._progress.say(f"{shot.id}: clip failed its check — {correction}")
        return replace(outcome, status="clip failed", clip=clip_path, clip_check="failed", problems=clip_problems)

    def _judge(self, campaign_id: str, shot: Shot, paths: list[str], last_frame: str = "") -> list[MediaVerdict]:
        """A verdict per file: the one on record where there is one, a check otherwise — a file
        is never paid to be judged twice."""
        recorded = self._store.verdicts(campaign_id)
        return [
            recorded[p] if p in recorded else self._checks.check(campaign_id, shot.id, p, last_frame) for p in paths
        ]

    def _companions(self, state: CampaignProgress, shot_id: str) -> tuple[str, ...]:
        """What else shows this person in this ad's outfit and light: the shoot sheet, then the
        other shots' chosen stills."""
        others = [o.still for sid, o in state.outcomes.items() if sid != shot_id and o.still]
        return tuple(([state.sheet] if state.sheet else []) + others)

    def _guard(self, campaign_id: str, budget: float) -> None:
        spent = self._store.spent(campaign_id)
        if spent >= budget:
            raise _BudgetReached(f"budget reached: ${spent:.2f} of ${budget:.2f}")

    def _report(self, campaign_id: str, state: CampaignProgress, stopped: str) -> CampaignReport:
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

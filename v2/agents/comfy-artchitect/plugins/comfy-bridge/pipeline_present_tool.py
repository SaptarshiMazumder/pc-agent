"""pipeline_present — the approval card, built from the design that was CHECKED, not from memory.

The end of Phase 1. Refuses unless the pipeline holds (pipeline_validate). Then it assembles the
arguments for `ask_user` — the card the window renders and the run gate waits on — from the stages
themselves:

    workflows   one row per stage: the agent's plain note on what it makes, and the model's short name
    references  the files the person adds (user slots), with what each must show
    questions   each stage's FULL prompt, so the person reads what will run and can edit it
    title       the job, the size it delivers (or what that size follows, when no stage sets it),
                and the download size

The agent passes them to ask_user unchanged and ends its turn. Nothing is downloaded or rented
before the answer: comfy_run refuses while the card is unanswered, and refuses a card that is not
this one (studio_state.checkpoint_answered, PipelineCard).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_workspace

import studio_state
from output_size_estimator import OutputSizeEstimator
from pipeline import Pipeline
from pipeline_tool_context import PipelineToolContext
from pipeline_validator import PipelineReport, render_report


#: ask_user's own limit on steps and on questions (plugins/ask: _MAX_ROWS).
CARD_ROWS = 8


class PipelinePresentTool(Tool):
    name = "pipeline_present"
    label = "Prepare the approval card"
    default_retryable = True
    description = (
        "When the design holds and every ? is answered: builds the approval card — every stage with "
        "its model, the files the person adds, each stage's full prompt, what it delivers and downloads. "
        "Give `answers`: for each ? in the report (q1, q2, …) one line on why the design is right as it "
        "is — or fix the design instead and validate again. Call ask_user with EXACTLY the arguments "
        "it returns, then end your turn."
    )
    parameters = {
        "type": "object",
        "properties": {
            "why": {
                "type": "string",
                "description": "One plain line on why these models, for the card's title (e.g. "
                               "'best free models for keeping a real face, then animating it').",
            },
            "answers": {
                "type": "object",
                "description": '{"q1": "one line on why this is right for the job", …} — one per ? in the '
                               "current report. A ? you agree with is fixed in the design, not answered.",
            },
        },
    }

    def __init__(self, context: Callable[[], PipelineToolContext] | None = None) -> None:
        self._context = context or (lambda: PipelineToolContext.for_workspace(Path(current_workspace(".") or ".")))

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            ctx = self._context()
            pipeline = ctx.store.load()
            if pipeline is None:
                return ToolResult.text("no pipeline in this chat yet — pipeline_plan first", is_error=True)
            report = ctx.validate(pipeline)
            if not report.holds:
                return ToolResult.text("the design does not hold yet — fix it before asking:\n"
                                       + render_report(report), is_error=True)
            # EVERY ? IS LOOKED AT. A finding the agent read past is how a video made straight from a
            # raw selfie reached the card with its warning still open.
            raw = params.get("answers") or {}
            if not isinstance(raw, dict):
                return ToolResult.text('`answers` is an object keyed by number: {"q1": "one line", "q2": "…"}',
                                       is_error=True)
            given = {str(k).strip().lower(): str(v).strip() for k, v in raw.items()}
            open_q = [f"q{i}: {q}" for i, q in enumerate(report.questions, 1) if not given.get(f"q{i}")]
            if open_q:
                return ToolResult.text(
                    "every ? needs an answer before the card — fix it in the design (then validate again), "
                    "or pass `answers` with one line per number on why it is right for this job. Unanswered:\n  "
                    + "\n  ".join(open_q), is_error=True)
            args = self.ask_arguments(ctx, pipeline, report, str(params.get("why") or "").strip())
            studio_state.record_card(args, [s.name for s in pipeline.stages])  # the gate checks the person saw THIS
            answered = "".join(f"\n  q{i}: {given[f'q{i}']}" for i in range(1, len(report.questions) + 1))
            return ToolResult.text(
                "Call ask_user with exactly these arguments — copy them character for character, title "
                "included: setup checks the person approved THIS card, and a reworded one (even a nicer "
                "title) is refused, so they would be asked twice. Then end your turn:\n"
                + json.dumps(args, indent=1, ensure_ascii=False)
                + (f"\n\nyour answers to the design's ? findings:{answered}" if answered else ""),
                details={"ask_user": args},
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"pipeline_present failed: {type(e).__name__}: {e}", is_error=True)

    @staticmethod
    def _what_you_get(ctx: PipelineToolContext, pipeline: Pipeline) -> str:
        """SEVERAL results the person ends up with, said plainly — five separate clips where they
        asked for one video is then visible on the card, before anything runs."""
        read = {i.producer[0] for s in pipeline.stages for i in s.inputs if i.producer}
        ends = [s for s in pipeline.stages if s.name not in read]
        if len(ends) < 2:
            return ""
        kinds = []
        for s in ends:
            try:
                types = set(ctx.builder.output_types(s).values())
            except Exception:  # noqa: BLE001 — a stage the check already reported
                types = set()
            kinds.append("video" if "VIDEO" in types else "image" if "IMAGE" in types else "file")
        counts = {k: kinds.count(k) for k in dict.fromkeys(kinds)}
        return ". You get " + " and ".join(f"{n} separate {k}{'s' if n > 1 else ''}" for k, n in counts.items())

    @staticmethod
    def _delivers(ctx: PipelineToolContext, pipeline: Pipeline) -> str:
        """The size the card promises — only one the design sets. A last stage whose canvas follows
        an input image (a reference editor with no size port) makes whatever that photo's shape is:
        the card said "Delivers 1080x1350" and the result was 880x1184."""
        if not pipeline.deliver_size or not pipeline.stages:
            return ""
        graphs = {s.name: ctx.store.graph(s) for s in pipeline.stages}
        got = OutputSizeEstimator(ctx.builder).sizes(pipeline, graphs).get(pipeline.stages[-1].name)
        if got is not None:
            return f". Delivers {got[0]}x{got[1]}"
        last = pipeline.stages[-1]
        follows = next((i.role.replace("_", " ") for i in last.inputs if not i.producer), "")
        return (f". Aims for {pipeline.deliver_size}, not guaranteed: the size follows "
                + (f"your {follows} image" if follows else "its input image"))

    @staticmethod
    def _download_text(report: PipelineReport) -> str:
        """The size the person reads. A file whose size the knowledge base does not know (a gated
        download) is said as such — '0 GB' for a 40 GB model is a number that is simply wrong."""
        unknown = sum(1 for b in report.files.values() if b is None)
        if not unknown:
            return f"{report.download_gb:.0f} GB"
        known = f"at least {report.download_gb:.0f} GB" if report.download_gb >= 1 else "an unknown amount"
        return f"{known} ({unknown} file size(s) unknown)"

    @staticmethod
    def _fit_card(workflows: list[dict], questions: list, keys: list[tuple]) -> tuple[list[dict], list[dict]]:
        """ask_user shows at most CARD_ROWS steps and CARD_ROWS questions. A longer design (five angles,
        five clips, a join) is shown with consecutive steps that run the same recipe as ONE row, and
        their prompts as one question, a line per step — every step and every prompt still on it."""
        steps = [q for q in questions if q]
        if len(workflows) <= CARD_ROWS and len(steps) <= CARD_ROWS:
            return workflows, [{k: v for k, v in q.items() if k != "_stage"} for q in steps]
        rows, asks, i = [], [], 0
        while i < len(workflows):
            j = i
            while j + 1 < len(workflows) and keys[j + 1] == keys[i]:
                j += 1
            group = workflows[i:j + 1]
            rows.append(group[0] if len(group) == 1 else {
                "name": f"{group[0]['name']} … {group[-1]['name']}",
                "does": (f"{len(group)} steps: " + "; ".join(w["does"].split(" — ")[0] for w in group)
                         + " — " + group[0]["does"].split(" — ", 1)[-1])[:300]})
            prompts = [q for q in questions[i:j + 1] if q]
            if len(prompts) == 1:
                asks.append({k: v for k, v in prompts[0].items() if k != "_stage"})
            elif prompts:
                asks.append({"question": "Prompts for " + ", ".join(q["_stage"].replace("_", " ") for q in prompts)
                             + " (one line each)",
                             "default": "\n".join(f"{q['_stage']}: {q['default']}" for q in prompts)})
            i = j + 1
        return rows, asks

    @staticmethod
    def ask_arguments(ctx: PipelineToolContext, pipeline: Pipeline, report: PipelineReport, why: str) -> dict:
        workflows, questions = [], []
        keys: list[tuple] = []  # per stage: what it runs, for grouping a long card
        for stage in pipeline.stages:
            keys.append((stage.family, stage.recipe) if not stage.custom else ("custom", stage.name))
            graph = ctx.store.graph(stage)
            prompt = ""
            if stage.custom:
                does = f"{stage.note} — custom workflow"
            else:
                recipe = ctx.builder.recipe_of(stage)
                fam = ctx.catalog.families[recipe.family]
                model = fam.display_name(recipe.id)
                does = f"{stage.note} — {model}"
                if stage.loras:
                    does += " with LoRA " + ", ".join(
                        lo.name.replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".safetensors")
                        + (f" @{lo.strength:g}" if lo.strength != 1.0 else "") for lo in stage.loras)
                spec = recipe.ports.get("prompt") or {}
                nid = spec.get("node") or (spec.get("nodes") or [None])[0]
                prompt = str(((graph.get(str(nid)) or {}).get("inputs") or {}).get(spec.get("input"), "")) if nid else ""
                # A FILE THAT BECOMES A FRAME is said on the card: the clip opens (or ends) on that very
                # picture, and the person is the one who knows whether that is what they meant.
                for i in stage.inputs:
                    frame = (recipe.inputs.get(i.name) or {}).get("frame")
                    if frame and not i.producer:
                        does += f" — {'opens' if frame == 'first' else 'ends'} on your {i.role.replace('_', ' ')} exactly as given"
            if stage.review:
                does += " (you see it before the next step)"
            workflows.append({"name": stage.name, "does": does[:300]})
            questions.append({"question": f"Prompt for {stage.name.replace('_', ' ')}", "default": prompt,
                              "_stage": stage.name} if len(prompt.split()) >= 8 else None)
        workflows, questions = PipelinePresentTool._fit_card(workflows, questions, keys)
        used_for: dict[str, list[str]] = {}  # role -> the notes of the steps that read it
        for stage in pipeline.stages:
            for i in stage.inputs:
                if not i.producer and stage.note not in used_for.setdefault(i.role, []):
                    used_for[i.role].append(stage.note)
        references = [{"role": role, "what": "for " + "; ".join(used_for.get(role) or [what])}
                      for role, what in sorted(report.user_inputs.items())]
        title = (f"{pipeline.name.replace('_', ' ')}: {len(pipeline.stages)} step(s)"
                 + (f" — {why}" if why else "")
                 + PipelinePresentTool._delivers(ctx, pipeline)
                 + PipelinePresentTool._what_you_get(ctx, pipeline)
                 + f". Downloads {PipelinePresentTool._download_text(report)}, all free.")
        if not questions:
            # A design with no prompt to read (an upscale, a restore) still needs one question: ask_user
            # refuses a card with nothing to answer, and the agent then rewrote the card to pass.
            questions.append({"question": "Anything to change before it is set up?",
                              "default": "Nothing — set it up as shown"})
        args = {"title": title, "workflows": workflows, "questions": questions}
        if references:
            args["references"] = references
        return args

__all__ = ["PipelinePresentTool"]

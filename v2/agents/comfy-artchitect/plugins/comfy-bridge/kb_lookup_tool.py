"""kb_lookup — the agent's knowledge of the models: which one for this job, and what it takes.

THE AGENT PICKS, NOT THE USER. Asked by TASK it returns the families ranked for it across the
knowledge base (TaskIndex: leaderboards, official claims, community evidence, each sourced), with
each family's own recipes for the task, their constraints (gated download, VRAM, disk) and
why. Asked for one RECIPE it returns what a stage built from it exposes — ports with their current
values, the media it takes and makes, its files and sizes, the node packs and ComfyUI it needs — and
the family's prompting guide. Free (open-weight) models only.

No GPU, no network: everything is the shipped knowledge base.
"""

from __future__ import annotations

import re

from collections.abc import Callable
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_workspace

from model_readiness import ModelReadiness
from pipeline_tool_context import PipelineToolContext

#: Index task id -> the profile `selection` task names that serve it.
_TASK_FAMILY_TASKS = {
    "t2i-photoreal": ("t2i",), "t2i-general": ("t2i",), "t2i-text": ("t2i",),
    "edit": ("edit",), "edit-identity": ("edit", "reference", "style-ref"),
    "compose-multi-image": ("edit", "reference"),
    "product-on-model": ("edit", "reference"),
    "t2v": ("t2v",), "i2v": ("i2v",), "flf2v": ("flf2v", "inpaint"),
    "reference-video": ("reference", "vace", "r2v"), "talking-head": ("ia2v", "s2v", "id-lora"),
    "video-with-audio": ("t2v", "i2v", "ia2v"), "upscale-image": ("upscale", "upscale-image"),
    "upscale-video": ("upscale", "upscale-video"), "control": ("control",), "inpaint": ("inpaint",),
    "outpaint": ("outpaint",), "join-clips": ("join-clips",), "stack-clips": ("stack-clips",), "multi-angle": ("multi-angle",), "control-map": ("control-map",), "t2i-360": ("t2i-360",), "video-character-replace": ("animate",), "motion-transfer": ("animate",), "video-object-replace": ("video-inpaint",), "frame-from-video": ("frame-from-video",),
}


#: What each task is FOR, in the words a brief uses — so the agent maps "a skybox", "the same
#: character from other angles", "replace the dancer" to the task that holds the right recipe.
_TASK_GLOSS = {
    "t2i-photoreal": "photographic image from text", "t2i-general": "any image from text",
    "t2i-text": "image with legible text (posters, signs, packaging)",
    "t2i-360": "360 / equirectangular panorama, skybox, VR environment",
    "edit": "change an image by instruction", "edit-identity": "keep a person/character/product while changing the rest",
    "compose-multi-image": "combine several images (put X into scene Y, outfit onto person)",
    "product-on-model": "a given person wearing or holding a given product (jacket, shirt, watch, headphones, bag): product ad, ad shot, lookbook, try-on, model with product, at an exact size or aspect ratio",
    "multi-angle": "the same subject or character from other camera angles: views, turnaround, angle sheet, front side back",
    "control": "image guided by edges/depth/pose of another image",
    "control-map": "make the edges/depth map a control input needs, from a raw photo or clip; keep a video's motion and layout while restyling it (watercolor, anime, clay, painting style)",
    "inpaint": "repaint a masked part of an image", "outpaint": "extend an image beyond its borders",
    "t2v": "video from text", "i2v": "video starting from an image, animate a photo",
    "flf2v": "video between a first and a last frame: morph or transition from one image to the next, keyframes",
    "reference-video": "video of a subject/product/sheet read as a reference (not as a frame)",
    "talking-head": "a person speaking, talking, saying a line or singing to camera (lip-sync, voice, dialogue)", "video-with-audio": "video with generated sound",
    "video-character-replace": "replace or swap the person/dancer in an existing video with a character, keep the moves and background",
    "motion-transfer": "make a still character move or dance like the person in a video",
    "video-object-replace": "change, swap or replace one object inside an existing clip or video, leave the rest as it is",
    "upscale-image": "enlarge, sharpen or restore an image: higher resolution, 2K, 4K, 8K",
    "upscale-video": "enlarge, sharpen, restore or clean up a video: higher resolution, 1080p, 4K, old or grainy footage",
    "frame-from-video": "one frame of a clip as an image (to restyle or edit it, then start a video from it)",
    "join-clips": "several clips played as one continuous video: stitch, merge, concatenate segments in order",
    "stack-clips": "several clips side by side in one frame: split screen, montage, grid, stacked videos",
}


_STOP = frozenset({"the", "and", "with", "for", "from", "into", "that", "this", "make", "want", "need", "please",
                   "my", "our", "your", "some", "same", "one", "all", "its", "out", "use", "using", "get"})


def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if len(word) > 4 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


class KbLookupTool(Tool):
    name = "kb_lookup"
    label = "Look up the best model for a job"
    default_retryable = True
    description = (
        "YOUR knowledge of the image and video models — use it instead of searching the web or "
        "recalling. Start with `query` (the job in plain words) to find the tasks and recipes that do it. "
        "Give `task` to get the free models ranked best-first for that kind of job, each "
        "with its recipes, why, and what can rule it out (gated download, VRAM, disk). "
        "Give `family` and `recipe` to see what a stage built from it exposes (ports you can set, "
        "media it takes and makes, files, prompting guide). Pick the top option that fits the job "
        "yourself and say your pick in one line — the user should never have to name a model. "
        "Tasks: " + "; ".join(f"{t} ({_TASK_GLOSS.get(t, '')})" for t in sorted(_TASK_FAMILY_TASKS)) + "."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The job in the person's own words (e.g. 'replace the dancer "
                      "in my video with my character') — finds the tasks and recipes that do it. Start here."},
            "task": {"type": "string", "enum": sorted(_TASK_FAMILY_TASKS),
                     "description": "The kind of job."},
            "family": {"type": "string", "description": "A family id, to list its recipes or (with recipe) see one."},
            "recipe": {"type": "string", "description": "A recipe id within `family`."},
        },
    }

    def __init__(self, context: Callable[[], PipelineToolContext] | None = None) -> None:
        self._context = context or (lambda: PipelineToolContext.for_workspace(Path(current_workspace(".") or ".")))

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            ctx = self._context()
            family = str(params.get("family") or "").strip()
            recipe = str(params.get("recipe") or "").strip()
            task = str(params.get("task") or "").strip()
            query = str(params.get("query") or "").strip()
            if query and not (family or task):
                return ToolResult.text(self._search(ctx, query))
            if family and recipe:
                return ToolResult.text(self._recipe(ctx, family, recipe))
            if family:
                return ToolResult.text(self._family(ctx, family, task))
            if task:
                return ToolResult.text(self._task(ctx, task))
            return ToolResult.text(
                "families: " + ", ".join(sorted(ctx.catalog.families))
                + "\ntasks: " + ", ".join(sorted(_TASK_FAMILY_TASKS))
                + "\nCall with `task` to get the ranked picks.")
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"kb_lookup failed: {type(e).__name__}: {e}", is_error=True)

    # ------------------------------------------------------------------ by words

    def _search(self, ctx: PipelineToolContext, query: str) -> str:
        """The tasks whose description, rankings and recipes share the most words with the job — so
        'replace the dancer in my video' finds video-character-replace, not the task the agent's own
        framing guessed."""
        words = {_stem(w) for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 2 and w not in _STOP}
        scored = []
        for task in _TASK_FAMILY_TASKS:
            blob = [task.replace("-", " "), _TASK_GLOSS.get(task, "")]
            for c in (ctx.task_index.ranked(task) if ctx.task_index else []):
                blob += [c.why, c.recipe_hint, " ".join(map(str, c.constraints))]
            for (fam_id, _rid), r in ctx.catalog.recipes.items():
                if r.task in _TASK_FAMILY_TASKS[task]:
                    blob.append(str(r.meta.get("variant", "")))
            own = {_stem(w) for w in re.findall(r"[a-z0-9]+", " ".join(blob[:2]).lower())}
            rest = {_stem(w) for w in re.findall(r"[a-z0-9]+", " ".join(blob[2:]).lower())}
            score = 3 * len(words & own) + len((words & rest) - own)  # the task's own words weigh most
            if score:
                scored.append((score, task))
        if not scored:
            return "no task matches those words — try kb_lookup with no arguments for the task list."
        lines = [f"tasks for '{query}', best match first:"]
        for n, task in sorted(scored, key=lambda x: (-x[0], x[1]))[:5]:
            top = (ctx.task_index.ranked(task) or [None])[0] if ctx.task_index else None
            pick = f" — top pick: {top.family}/{top.recipe}" if top and top.recipe else ""
            lines.append(f"  {task}: {_TASK_GLOSS.get(task, '')}{pick}")
        lines.append("Then kb_lookup task=<one of these> for the ranked picks, and family+recipe for the detail.")
        return "\n".join(lines)

    # ------------------------------------------------------------------ by task

    @staticmethod
    def _cloud_files(ctx: PipelineToolContext) -> set[str] | None:
        """The model files Comfy Cloud lists in its loaders, by base name — from its node list as
        captured (NodeRegistryCache); None when only the shipped list is at hand, which says nothing
        about what Comfy Cloud has."""
        if "captured" not in str(ctx.catalogue_source):
            return None
        names: set[str] = set()
        for spec in (ctx.catalogue or {}).values():
            inputs = (spec or {}).get("input") or {} if isinstance(spec, dict) else {}
            for section in ("required", "optional"):
                for entry in (inputs.get(section) or {}).values():
                    values = entry[0] if isinstance(entry, list) and entry else None
                    if isinstance(values, list):
                        names.update(str(v).replace("\\", "/").rsplit("/", 1)[-1] for v in values
                                     if isinstance(v, str) and v.lower().endswith(ModelReadiness.EXTENSIONS))
        return names or None

    @staticmethod
    def _cloud_note(recipe, cloud: set[str] | None, constraints: list) -> list:
        """A recipe's constraints as they stand ON COMFY CLOUD: 'gated' is a download hurdle, and
        a model Comfy Cloud already has is not downloaded — the label had the agent pass over the
        best model for a 'non-gated' one. A file Comfy Cloud lacks is said instead."""
        if recipe is None or cloud is None:
            return list(constraints)
        missing = [f for f in recipe.files if f.replace("\\", "/").rsplit("/", 1)[-1] not in cloud]
        kept = [c for c in constraints if not (str(c).strip().lower() == "gated" and not missing)]
        if missing:
            kept.append(f"Comfy Cloud lacks {len(missing)} of its files ({', '.join(missing[:3])}"
                        f"{', …' if len(missing) > 3 else ''}): comfy_install imports them (Creator plan)")
        return kept

    def _task(self, ctx: PipelineToolContext, task: str) -> str:
        wanted = _TASK_FAMILY_TASKS.get(task, (task,))
        lines = [f"best free models for {task}, best first:"]
        ranked = ctx.task_index.ranked(task) if ctx.task_index else []
        cloud = self._cloud_files(ctx)
        listed = set()
        for choice in ranked:
            fam = ctx.catalog.families.get(choice.family)
            if fam is None:
                continue
            listed.add(fam.id)
            recipe = ctx.catalog.recipe(fam.id, choice.recipe) if choice.recipe else None
            # NOT RUNNABLE HERE is said, not hidden: the agent skips it, and can tell the user why.
            if recipe is None:
                avail = "  [NOT AVAILABLE here: no recipe for this yet]"
            elif recipe.meta.get("blocked"):
                avail = f"  [NOT AVAILABLE here: {recipe.meta['blocked']}]"
            elif not recipe.runs_on(ctx.comfyui_version):
                avail = (f"  [NOT AVAILABLE here: needs ComfyUI {recipe.meta.get('min_comfyui')}, "
                         f"the box runs {ctx.comfyui_version}]")
            else:
                avail = ""
            if choice.route:
                lines.append(f"{choice.rank}. ROUTE ({len(choice.route)} stages) — {choice.why}")
                notes: list = list(choice.constraints)
                for n, step in enumerate(choice.route, start=1):
                    r = ctx.catalog.recipe(str(step.get("family")), str(step.get("recipe")))
                    gone = "" if r is not None and r.runs_on(ctx.comfyui_version) else "  [NOT AVAILABLE here]"
                    lines.append(f"     stage {n}: family={step.get('family')} recipe={step.get('recipe')} — "
                                 f"{step.get('does', '')}{gone}")
                    notes += [c for c in self._cloud_note(r, cloud, []) if c not in notes]
                if notes:
                    lines.append("     watch: " + "; ".join(str(c) for c in notes))
                if choice.evidence:
                    ev = choice.evidence[0]
                    lines.append(f"     evidence: {ev.get('class', '')} {ev.get('src', '')} {ev.get('date', '')}".rstrip())
                continue
            lines.append(f"{choice.rank}. {fam.name} — {choice.why}{avail}")
            if recipe is not None:
                lines.append(f"     start from: family={fam.id} recipe={recipe.id}")
            constraints = self._cloud_note(recipe, cloud, choice.constraints)
            if constraints:
                lines.append("     watch: " + "; ".join(str(c) for c in constraints))
            if choice.evidence:
                ev = choice.evidence[0]
                lines.append(f"     evidence: {ev.get('class', '')} {ev.get('src', '')} {ev.get('date', '')}".rstrip())
        rest = [f for f in ctx.catalog.families.values() if f.id not in listed and self._rows(f, wanted)]
        if rest:
            lines.append("also covers it (not ranked for this task):" if ranked else
                         "NO CROSS-FAMILY RANKING for this task yet — each family's own picks, unranked:")
            for fam in rest:
                rows = self._rows(fam, wanted)
                lines.append(f"  {fam.name} ({fam.id}): " + ", ".join(s["recipe"] for s in rows[:5]))
        if len(lines) == 1:
            lines.append("  no family in the knowledge base covers this task")
        lines.append("kb_lookup family=<id> recipe=<id> shows a recipe's ports, inputs, outputs, files and prompting.")
        return "\n".join(lines)

    @staticmethod
    def _rows(fam, wanted) -> list[dict]:
        return [s for s in fam.selection if s.get("recipe") and str(s.get("task")) in wanted
                and "pending" not in str(s.get("status") or "").lower()
                and "needs comfyui" not in str(s.get("status") or "").lower()]

    # ------------------------------------------------------------------ by family / recipe

    def _family(self, ctx: PipelineToolContext, family: str, task: str) -> str:
        fam = ctx.catalog.families.get(family)
        if fam is None:
            return f"no family '{family}'. Families: {', '.join(sorted(ctx.catalog.families))}"
        lines = [f"{fam.name} ({fam.id})"]
        for s in fam.selection:
            if task and str(s.get("task")) not in _TASK_FAMILY_TASKS.get(task, (task,)):
                continue
            status = f" [{s['status']}]" if s.get("status") else ""
            lines.append(f"  {s.get('task')}: {s.get('recipe') or '(none)'}{status} — "
                         f"{str(s.get('why') or s.get('tradeoff') or '')[:180]}")
        return "\n".join(lines)

    def _recipe(self, ctx: PipelineToolContext, family: str, recipe_id: str) -> str:
        r = ctx.catalog.recipe(family, recipe_id)
        if r is None:
            have = ", ".join(sorted(x.id for x in ctx.catalog.recipes_of(family)))
            return f"no recipe '{recipe_id}' in {family}. It has: {have or 'none'}"
        fam = ctx.catalog.families[family]
        lines = [f"{family}/{r.id} — {r.meta.get('variant', '')}",
                 f"  from: {r.meta.get('from_template', '')} ({r.meta.get('confidence', '')}); "
                 f"needs ComfyUI {r.meta.get('min_comfyui', '?')}"]
        if r.meta.get("defaults_note"):
            lines.append(f"  defaults: {r.meta['defaults_note']}")
        lines.append("  ports (stage_set these):")
        for name, spec in r.ports.items():
            nid = spec.get("node") or (spec.get("nodes") or [None])[0]
            value = (r.graph.get(str(nid)) or {}).get("inputs", {}).get(spec.get("input")) if nid else None
            shown = repr(value) if not isinstance(value, str) or len(value) < 80 else repr(value[:77] + "…")
            opts = f" one of {spec['options']}" if spec.get("options") else ""
            lines.append(f"    {name} = {shown}{opts}" + (f" — {spec['note']}" if spec.get("note") else ""))
        lines.append("  takes (bind with stage_bind): " + (", ".join(
            f"{n} ({s.get('type')}" + (f", becomes the {s['frame'].upper()} frame" if s.get("frame") else "")
            + (f", a PREPARED {s['prepared']} signal" if s.get("prepared") else "")
            + (f", RAW footage/photo: computes the {s['raw']} itself" if s.get("raw") else "") + ")"
            for n, s in r.inputs.items()) or "nothing"))
        lines.append("  makes: " + ", ".join(f"{n} ({s.get('type')})" for n, s in r.outputs.items()))
        size = sum(ctx.catalog.file_bytes(f) or 0 for f in r.files)
        lines.append(f"  files: {len(r.files)}, {size / 1e9:.1f} GB" + (f"; packs: {r.packs}" if r.packs else ""))
        ve = r.meta.get("vram_evidence") or {}
        if ve:
            lines.append(f"  measured: {ve.get('gpu')} at {ve.get('at')}: {ve.get('usage')}"
                         + (f", {ve.get('time_s')} s" if ve.get("time_s") else ""))
        # THE FAMILY'S OTHER JOBS, so a specialised recipe (a camera-angle LoRA, a 360 LoRA) is found
        # from whichever task the agent looked up first — not only when it guesses the right task name.
        others: dict[str, list[str]] = {}
        for other in ctx.catalog.recipes_of(fam.id):
            if other.task != r.task:
                others.setdefault(other.task, []).append(other.id)
        if others:
            lines.append("  this family also does: " + "; ".join(
                f"{task} ({', '.join(sorted(ids)[:4])}{', …' if len(ids) > 4 else ''})" for task, ids in sorted(others.items())))
        guide = fam.prompting_guide()
        if guide:
            lines.append("\n" + guide)
        return "\n".join(lines)


__all__ = ["KbLookupTool"]

"""lora_search — LoRAs trained for the model a stage runs: Comfy Cloud's own first, then Civitai's.

A style, a character or an effect the base model cannot hold reliably is a LoRA's job — and a LoRA
only works on the model it was trained for (ComfyUI applies a wrong one silently, to no effect). So
the search starts from the STAGE'S MODEL, not from the words: the family (and recipe) give the
model, its profile's `lora` block gives Civitai's base tags and Comfy Cloud's naming for it, and
only LoRAs of that model are listed.

  * COMFY CLOUD first: its preinstalled LoRAs (the captured node list's `lora_name` choices) need
    no download and no plan that imports. Matched by the model's naming and the query's words.
  * CIVITAI next: the most-downloaded safe-for-work LoRAs of the model's bases matching the query,
    each with what a stage's `loras` entry needs — file name, base, download link, trained words —
    and the author's own words on strength. Setup imports the file (the person's Civitai key).
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_workspace

from civitai_client import CivitaiClient, CivitaiError
from pipeline_tool_context import PipelineToolContext

#: Comfy Cloud LoRAs listed per search — the rest are counted.
CLOUD_SHOWN = 25
#: Word pairs a long query is retried as on Civitai, at most.
MAX_PAIRS = 4


class LoraSearchTool(Tool):
    name = "lora_search"
    label = "Find LoRAs"
    default_retryable = True
    description = (
        "Find LoRAs for the model a stage runs — a style, a character, a look, an effect. Give the "
        "stage's `family` (and `recipe`, to narrow to its exact model) and `query` in a few words "
        "('retro anime', 'watercolor', 'film grain'). Lists Comfy Cloud's preinstalled LoRAs for "
        "that model first (no download), then Civitai's most-downloaded ones trained on it, each "
        "with the `loras` entry pipeline_plan / stage_set take. Only LoRAs of that model are listed: "
        "a LoRA for another model does nothing."
    )
    parameters = {
        "type": "object",
        "required": ["family"],
        "properties": {
            "family": {"type": "string", "description": "the stage's knowledge-base family, e.g. z-image"},
            "recipe": {"type": "string", "description": "the stage's recipe, to narrow to its model (klein 9B vs 4B, Wan T2V vs I2V)"},
            "query": {"type": "string", "description": "the look in 1-3 words ('cel anime', 'watercolor', 'film grain') — search again with other words rather than one long phrase; empty = the most used"},
            "limit": {"type": "integer", "description": "Civitai results, 1-20 (default 8)"},
        },
    }

    def __init__(self, civitai: CivitaiClient,
                 context: Callable[[], PipelineToolContext] | None = None) -> None:
        self._civitai = civitai
        self._context = context or (lambda: PipelineToolContext.for_workspace(Path(current_workspace(".") or ".")))

    async def execute(self, tool_call_id, params, abort, on_update=None):
        ctx = self._context()
        family = str(params.get("family") or "").strip()
        fam = ctx.catalog.families.get(family)
        if fam is None:
            return ToolResult.text(f"no knowledge-base family '{family}' (families: "
                                   f"{', '.join(sorted(ctx.catalog.families))})", is_error=True)
        if not fam.lora:
            return ToolResult.text(f"{family} takes no LoRA in this knowledge base", is_error=True)
        models, problem = self._models(ctx, fam, str(params.get("recipe") or "").strip())
        if problem:
            return ToolResult.text(problem, is_error=True)
        query = str(params.get("query") or "").strip()
        limit = max(1, min(int(params.get("limit") or 8), 20))
        bases = sorted({b for m in models for b in m.get("bases") or []})
        names = ", ".join(str(m.get("name")) for m in models)
        lines = [f"LoRAs for {names} — Civitai base{'s' if len(bases) != 1 else ''}: {' / '.join(bases) or 'none'}"]
        if fam.lora.get("note"):
            lines.append(f"  {fam.lora['note']}")

        cloud, cloud_note = self._cloud(ctx, models, query)
        lines.append("")
        lines.append(cloud_note)
        lines += [f"  {n}" + (f"  (matches: {', '.join(hit)})" if hit else "") for n, hit in cloud[:CLOUD_SHOWN]]
        if len(cloud) > CLOUD_SHOWN:
            lines.append(f"  … and {len(cloud) - CLOUD_SHOWN} more (narrow the query)")

        found = []
        lines.append("")
        if not bases:
            lines.append("Civitai: no base tag exists for this model — only Comfy Cloud's LoRAs above apply.")
        else:
            try:
                found, asked = self._civitai_search(bases, query, limit)
            except CivitaiError as e:
                lines.append(f"Civitai: {e} — try again, or use Comfy Cloud's LoRAs above.")
            else:
                searched = f" (searched: {' / '.join(repr(q) for q in asked)})" if asked != [query] else ""
                lines.append(f"On Civitai — imported at setup with the person's Civitai key ({len(found)}){searched}:"
                             if found else f"Civitai: nothing trained on {' / '.join(bases)} matches "
                             f"{' / '.join(repr(q) for q in asked)}.")
                for i, lo in enumerate(found, 1):
                    liked = f", {round(100 * lo.liked / (lo.liked + lo.disliked))}% liked" if lo.liked + lo.disliked else ""
                    lines.append(f"  {i}. {lo.name} — {lo.version} [{lo.base}] {lo.downloads:,} downloads{liked}, "
                                 f"{lo.size_mb:g} MB")
                    entry = {"name": lo.file, "base": lo.base, "url": lo.download_url}
                    if lo.trained_words:
                        entry["trigger"] = lo.trained_words[0]
                    lines.append(f"     loras entry: {entry}")
                    if len(lo.trained_words) > 1:
                        lines.append(f"     trained words: {', '.join(lo.trained_words[:8])}")
                    for other in lo.others:
                        lines.append(f"     also {other.version}: {{'name': {other.file!r}, 'base': {other.base!r}, "
                                     f"'url': {other.download_url!r}}}")
                    if lo.strength_hint:
                        lines.append(f"     author on strength: \"{lo.strength_hint}\"")
                    lines.append(f"     {lo.page}")
        lines.append("")
        lines.append("Put the pick in the stage's `loras` (pipeline_plan / stage_set): a Comfy Cloud one by "
                     "`name`; a Civitai one with its `name`, `base`, `url` and `trigger` — and the trigger in "
                     "the prompt. Strength 1.0 unless the author says otherwise; two-expert models take a "
                     "high/low pair (`expert`).")
        return ToolResult.text("\n".join(lines), details={
            "cloud": [n for n, _ in cloud], "civitai": [dataclasses.asdict(lo) for lo in found], "bases": bases})

    # ------------------------------------------------------------------ Civitai

    def _civitai_search(self, bases: list[str], query: str, limit: int) -> tuple[list, list[str]]:
        """Civitai's LoRAs for the query — and when a phrase of several words finds nothing (its
        search wants every word in a name), for each pair of neighbouring words, merged by downloads."""
        found = self._civitai.search_loras(bases, query, limit)
        words = query.split()
        if found or len(words) < 3:
            return found, [query]
        asked, merged = [], {}
        for pair in [" ".join(words[i:i + 2]) for i in range(len(words) - 1)][:MAX_PAIRS]:
            asked.append(pair)
            for lo in self._civitai.search_loras(bases, pair, limit):
                merged.setdefault(lo.version_id, lo)
        return sorted(merged.values(), key=lambda lo: -lo.downloads)[:limit], [query] + asked

    # ------------------------------------------------------------------ the model

    @staticmethod
    def _models(ctx: PipelineToolContext, fam, recipe_id: str) -> tuple[list[dict], str]:
        """The family's LoRA models the recipe loads (all of them without a recipe)."""
        models = list(fam.lora.get("models") or [])
        if not recipe_id:
            return models, ""
        recipe = ctx.catalog.recipe(fam.id, recipe_id)
        if recipe is None:
            return [], (f"{fam.id} has no recipe '{recipe_id}' (it has: "
                        f"{', '.join(sorted(r.id for r in ctx.catalog.recipes_of(fam.id)))})")
        files = [f.replace("\\", "/").rsplit("/", 1)[-1] for f in recipe.files]
        hit = [m for m in models if any(re.search(str(m.get("file") or "$^"), f, re.I) for f in files)]
        if not hit:
            return [], f"recipe {fam.id}/{recipe_id} loads no model that takes a LoRA ({', '.join(files)})"
        return hit, ""

    # ------------------------------------------------------------------ Comfy Cloud

    @staticmethod
    def _cloud(ctx: PipelineToolContext, models: list[dict], query: str) -> tuple[list[tuple[str, list[str]]], str]:
        """Comfy Cloud's preinstalled LoRAs for these models, as (name, the query words it carries),
        most words first; and the heading."""
        if "captured" not in str(ctx.catalogue_source):
            return [], ("Comfy Cloud: its LoRA list is not captured yet (comfy_probe captures it) — "
                        "only Civitai below.")
        spec = (ctx.catalogue.get("LoraLoaderModelOnly") or {}).get("input") or {}
        choices = ((spec.get("required") or {}).get("lora_name") or [[]])[0]
        patterns = [p for m in models for p in m.get("cloud") or []]
        mine = [str(n) for n in choices if isinstance(n, str)
                and any(re.search(p, n.replace("\\", "/").rsplit("/", 1)[-1], re.I) for p in patterns)]
        head = "On Comfy Cloud — no download; a `loras` entry is just {'name': <file>}"
        words = [w for w in re.split(r"[^a-z0-9]+", query.lower()) if len(w) > 1]
        if not words:
            return [(n, []) for n in sorted(mine)], f"{head} ({len(mine)} for this model):"
        scored = sorted(((n, [w for w in words if w in n.lower()]) for n in mine), key=lambda x: (-len(x[1]), x[0]))
        hits = [(n, hit) for n, hit in scored if hit]
        if not hits:
            return [], f"On Comfy Cloud: none of its {len(mine)} LoRAs for this model is named for '{query}'."
        weak = (" — a name sharing one word of several is a weak fit: pick it only if the name means the look"
                if len(words) > 1 and len(hits[0][1]) == 1 else "")
        return hits, f"{head} ({len(hits)} of its {len(mine)} for this model share words with '{query}'{weak}):"


__all__ = ["LoraSearchTool"]

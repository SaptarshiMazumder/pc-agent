"""reference_recipe — how a reference image was made, read from the image itself, ready to recreate.

"Make images like this one" with a Civitai link is the most exact brief there is: most Civitai
images carry their recipe — the model, the LoRAs and their strengths, the prompts, the sampler and
size. This reads it and hands the design what it needs to recreate the look faithfully:

  1. CIVITAI'S RECORD first (`/images?imageId=…&withMeta=true`): for a ComfyUI upload it keeps the
     whole graph; for others, the prompt, settings and resources;
  2. else THE ORIGINAL FILE: downloaded (`original=true` — every resized copy is stripped) and read
     by its bytes (ImageMetadataReader). A file already in the chat (an upload) is read directly;
  3. every LoRA RESOLVED on Civitai — by version id, by hash, or by its file name — to its base
     model, download link and trained words; Comfy Cloud's own LoRAs with the same words are named;
  4. the base model mapped to the knowledge base: the family, model and recipes that run it.

ON THE WEB a file downloaded in this call is not readable until the next call (the sandbox got
its copy of the workspace before the download): the result then says to call again with the path.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_workspace

import chat_paths
from civitai_client import CivitaiClient, CivitaiError, CivitaiLora
from image_metadata_reader import ImageMetadataReader
from image_recipe import ImageRecipe, ImageRecipeLora
from image_recipe_parser import ImageRecipeParser
from pipeline_tool_context import PipelineToolContext

_CIVITAI_IMAGE = re.compile(r"(?:civitai\.com/images/|^)(\d{3,})\b")
#: LoRAs resolved per image — a stack longer than this is a collage, not a recipe.
MAX_LORAS = 8


class ReferenceRecipeTool(Tool):
    name = "reference_recipe"
    label = "Read an image's recipe"
    default_retryable = True
    description = (
        "Read how a reference image was made — model, LoRAs with strengths, prompts, sampler, steps, "
        "guidance, size — from a Civitai image link (civitai.com/images/<id>) or an image file in "
        "this chat. Each LoRA comes back resolved (base model, download link, trigger words) as a "
        "ready `loras` entry, with the knowledge-base family and recipes that run its model. Use it "
        "whenever the person gives a Civitai link or an image to 'make more like': recreate the "
        "recipe faithfully before changing anything."
    )
    parameters = {
        "type": "object",
        "required": ["source"],
        "properties": {
            "source": {"type": "string", "description": "a civitai.com/images/<id> link, the id, or a file path in this chat"},
        },
    }

    def __init__(self, civitai: CivitaiClient, download: Callable[[str, str], object],
                 context: Callable[[], PipelineToolContext] | None = None) -> None:
        """:param download: (url, workspace-relative path) -> Response — the original file, saved."""
        self._civitai = civitai
        self._download = download
        self._context = context or (lambda: PipelineToolContext.for_workspace(Path(current_workspace(".") or ".")))

    async def execute(self, tool_call_id, params, abort, on_update=None):
        ctx = self._context()
        source = str(params.get("source") or "").strip()
        try:
            m = _CIVITAI_IMAGE.search(source) if ("civitai.com" in source or source.isdigit()) else None
            if m:
                recipe, base, note = self._from_civitai(ctx, int(m.group(1)))
            else:
                recipe, base, note = self._from_file(ctx, source), "", ""
        except (CivitaiError, ValueError) as e:
            return ToolResult.text(f"reference_recipe: {e}", is_error=True)
        if recipe is None:
            return ToolResult.text(note or f"{source} carries no generation record (its metadata was "
                                   "stripped): recreate it from what it shows — kb_lookup and lora_search.",
                                   is_error=not note)
        resolved = self._resolve(recipe, self._cloud_loras(ctx))
        checkpoints = self._checkpoints(recipe)
        return ToolResult.text(self._render(ctx, recipe, base, resolved, checkpoints, note), details={
            "settings": recipe.settings(), "models": recipe.models,
            "loras": [{"as_named": lo.name, "strength": lo.strength, "lookup_failed": failed, "on_comfy_cloud": cloud,
                       "civitai": dataclasses.asdict(hit) if hit else None} for lo, hit, failed, cloud in resolved]})

    # ------------------------------------------------------------------ reading

    def _from_civitai(self, ctx: PipelineToolContext, image_id: int) -> tuple[ImageRecipe | None, str, str]:
        image = self._civitai.image(image_id)
        recipe = ImageRecipeParser.from_civitai_meta(image.meta)
        if recipe is None or recipe.empty:
            ext = Path(image.url.split("?", 1)[0]).suffix or ".img"
            rel = f"{chat_paths.chat_rel(chat_paths.REFERENCES)}/civitai_{image_id}_original{ext}"
            res = self._download(image.url, rel)
            if not res.ok:
                raise CivitaiError(f"Civitai keeps no record for image {image_id}, and its original file did "
                                   f"not download (HTTP {res.status} {res.error})")
            path = Path(ctx.workspace) / rel
            if not path.is_file():
                return None, image.base, (f"Civitai keeps no record for image {image_id}; its original file is "
                                          f"saved at {rel} — call reference_recipe again with source={rel} to "
                                          "read the recipe inside it.")
            recipe = ImageRecipeParser.from_chunks(ImageMetadataReader.read(path.read_bytes()))
            if recipe is None:
                return None, image.base, ""
        recipe.width = recipe.width or image.width or None
        recipe.height = recipe.height or image.height or None
        return recipe, image.base, ""

    @staticmethod
    def _from_file(ctx: PipelineToolContext, rel: str) -> ImageRecipe | None:
        root = Path(ctx.workspace).resolve()
        path = (root / rel).resolve()
        if root not in path.parents or not path.is_file():
            raise ValueError(f"no file {rel!r} in this chat — give a civitai.com/images/<id> link or a path "
                             f"under {chat_paths.chat_rel(chat_paths.REFERENCES)}/")
        data = path.read_bytes()
        if not ImageMetadataReader.format_of(data):
            raise ValueError(f"{rel} is not a PNG, JPEG or WebP image")
        return ImageRecipeParser.from_chunks(ImageMetadataReader.read(data))

    # ------------------------------------------------------------------ resolving

    def _resolve(self, recipe: ImageRecipe, cloud: list[str]) -> list[tuple[ImageRecipeLora, CivitaiLora | None, str, str]]:
        """(the LoRA as the image names it, its Civitai version, why the lookup failed, the name
        Comfy Cloud has it under) — a failed lookup is that LoRA's to report: the recipe read from
        the image stands without it. A LoRA whose very file Comfy Cloud has (a speed LoRA from
        Hugging Face, `Wan2.2-Lightning_…`) is that file: nothing to look up or import."""
        on_cloud = {_stem(n): n for n in cloud}
        out, seen = [], set()
        for lo in recipe.loras[:MAX_LORAS]:
            if lo.name in seen:
                continue  # a graph that loads one LoRA in several places names it once here
            seen.add(lo.name)
            if _stem(lo.name) in on_cloud:
                out.append((lo, None, "", on_cloud[_stem(lo.name)]))
                continue
            hit, failed = None, ""
            try:
                if lo.version_id:
                    hit = self._civitai.version(lo.version_id)
                if hit is None and lo.hash:
                    hit = self._civitai.version_by_hash(lo.hash)
                if hit is None and lo.name.lower().endswith(".safetensors"):
                    hit = self._civitai.lora_by_file(lo.name)
            except CivitaiError as e:
                failed = str(e)
            out.append((lo, hit, failed, ""))
        return self._one_per_lora(out)

    def _one_per_lora(self, rows: list) -> list:
        """A LoRA the record names TWICE — by its short name in the prompt (`<lora:gr33nXLP:1>`) and
        by its Civitai version — is one LoRA: the unresolved name whose file the resolved version
        ships is dropped. A short name nothing resolved is then looked up as `<name>.safetensors`."""
        stems = {_stem(hit.file) for _, hit, _, _ in rows if hit is not None}
        out = []
        for lo, hit, failed, cloud in rows:
            if cloud:
                out.append((lo, hit, failed, cloud))
                continue
            if hit is None and not failed and _stem(lo.name) in stems:
                continue
            if hit is None and not failed and not lo.version_id and not lo.hash and "." not in _stem_tail(lo.name):
                try:
                    hit = self._civitai.lora_by_file(f"{_stem(lo.name)}.safetensors")
                except CivitaiError as e:
                    failed = str(e)
            out.append((lo, hit, failed, ""))
        return out

    def _checkpoints(self, recipe: ImageRecipe) -> list[tuple[int, CivitaiLora | None, str]]:
        """(version id, the checkpoint's Civitai version, why the lookup failed) for the record's
        checkpoint(s)."""
        out = []
        for vid in recipe.model_version_ids[:2]:
            try:
                out.append((vid, self._civitai.version(vid), ""))
            except CivitaiError as e:
                out.append((vid, None, str(e)))
        return out

    # ------------------------------------------------------------------ the answer

    def _render(self, ctx: PipelineToolContext, recipe: ImageRecipe, image_base: str,
                resolved: list[tuple[ImageRecipeLora, CivitaiLora | None, str, str]],
                checkpoints: list[tuple[int, CivitaiLora | None, str]], note: str) -> str:
        lines = [f"Recipe, from {recipe.source}:"]
        bases = [b for b in [image_base, *(c.base for _, c, _ in checkpoints if c),
                             *(h.base for _, h, _, _ in resolved if h)] if b]
        if recipe.models:
            lines.append(f"  model: {', '.join(recipe.models)}" + (f" [{image_base}]" if image_base else ""))
        elif image_base:
            lines.append(f"  model: {image_base} (Civitai's tag; the file is not named)")
        lines += self._checkpoint_lines(ctx, checkpoints)
        settings = recipe.settings()
        if settings:
            lines.append("  settings: " + ", ".join(f"{k} {v}" for k, v in settings.items())
                         + (" (clip_skip is the port's value: the record's 'clip skip "
                            f"{-recipe.clip_skip}')" if recipe.clip_skip not in (None, -1) else ""))
        lines += self._unknown_sampling(ctx, recipe)
        if recipe.prompt:
            lines.append(f"  prompt: {recipe.prompt}")
        if recipe.negative:
            lines.append(f"  negative: {recipe.negative}")
        custom = sorted(c for c in recipe.classes if c not in (ctx.catalogue or {}))
        if custom:
            lines.append(f"  custom nodes (not on Comfy Cloud — their values are read above, the nodes are not "
                         f"needed): {', '.join(custom)}")
        if resolved:
            lines.append("")
            lines.append("LoRAs:")
            cloud = self._cloud_loras(ctx)
            for lo, hit, failed, on_cloud in resolved:
                if on_cloud:
                    lines.append(f"  {lo.name} @{lo.strength:g} — Comfy Cloud has this very file: loras entry "
                                 f"{{'name': {on_cloud!r}, 'strength': {lo.strength:g}}} (no download)")
                    continue
                if failed:
                    file = lo.name.replace("\\", "/").rsplit("/", 1)[-1]
                    keep = {"name": file, "strength": lo.strength, **({"base": image_base} if image_base else {})}
                    lines.append(f"  {lo.name} @{lo.strength:g} — the Civitai lookup FAILED ({failed}). This is the "
                                 f"image's LoRA all the same: call reference_recipe again in a moment for its "
                                 f"download link, or keep it by name — {keep} — which runs only if it is already "
                                 "in the person's Comfy Cloud (lora_search lists their imports)")
                    continue
                if hit is None:
                    lines.append(f"  {lo.name} @{lo.strength:g} — NOT found on Civitai (a private or renamed file): "
                                 "find the closest with lora_search, and say so")
                    continue
                entry = {"name": hit.file, "strength": lo.strength, "base": hit.base, "url": hit.download_url}
                if hit.trained_words:
                    entry["trigger"] = hit.trained_words[0]
                lines.append(f"  {hit.name} — {hit.version} [{hit.base}] @{lo.strength:g}")
                lines.append(f"     loras entry: {entry}")
                if len(hit.trained_words) > 1:
                    lines.append(f"     trained words: {', '.join(hit.trained_words[:8])}")
                lines.append(f"     {hit.page}")
                same = self._similar(hit.file, self._cloud_for_base(ctx, cloud, hit.base))
                if same:
                    lines.append(f"     Comfy Cloud has {', '.join(same)} — perhaps the same LoRA under another "
                                 "name (unverified); the Civitai file is the faithful one")
            if len(recipe.loras) > MAX_LORAS:
                lines.append(f"  … {len(recipe.loras) - MAX_LORAS} more not resolved")
        lines.append("")
        lines += self._families(ctx, list(dict.fromkeys(bases)), recipe.models)
        lines.append("")
        lines.append("To recreate it faithfully: one stage of that recipe with this prompt, size, steps, "
                     "guidance/cfg, sampler, scheduler, clip_skip and seed set through its ports (kb_lookup "
                     "shows their names); the image's model file in the port named above with its "
                     "`models` entry when it is not the recipe's own; and THESE LoRAs at THESE strengths in `loras`, exactly as listed — they ARE the "
                     "look; no other LoRA replaces or joins them unless one above was not found. Then change "
                     "only what the person asked to change.")
        if note:
            lines.append(note)
        return "\n".join(lines)

    @staticmethod
    def _families(ctx: PipelineToolContext, bases: list[str], models: list[str]) -> list[str]:
        """The knowledge-base family, model and recipes that run what the image was made with: the
        model TRAINED as that base first (FLUX.1 dev for "Flux.1 D", before schnell and Krea, which
        merely take its LoRAs), a family's own text-to-image recipes before its other ones."""
        rows = []
        for base in bases:
            for fam in ctx.catalog.families.values():
                for model in fam.lora.get("models") or []:
                    model_bases = list(model.get("bases") or [])
                    if base not in model_bases:
                        continue
                    pattern = str(model.get("file") or "$^")
                    port = str(model.get("via_port") or "")
                    recipes = [r for r in ctx.catalog.recipes_of(fam.id)
                               if (port and port in r.ports) or
                               any(re.search(pattern, f.replace("\\", "/").rsplit("/", 1)[-1], re.I) for f in r.files)]
                    if not recipes:
                        continue
                    picks = [str(s.get("recipe")) for s in fam.selection if s.get("task") == "t2i"]
                    t2i = sorted((r.id for r in recipes if r.task == "t2i"),
                                 key=lambda rid: (picks.index(rid) if rid in picks else len(picks), rid))
                    rest = sorted(r.id for r in recipes if r.task != "t2i")
                    text = f"  {base} → {fam.id} ({model.get('name')}): " + (
                        f"text-to-image {', '.join(t2i)}" + (f"; also {', '.join(rest[:8])}" if rest else "")
                        if t2i else ", ".join(rest[:10])) + (
                        f" — the image's checkpoint goes in their `{port}` port" if port else "")
                    rank = (model_bases.index(base) != 0, fam.id in ("controlnet", "identity-adapters"), not t2i)
                    rows.append((rank, text))
        out = [text for _, text in sorted(rows, key=lambda r: r[0])]
        for name in models:
            found = ctx.catalog.describe_file(name.replace("\\", "/").rsplit("/", 1)[-1])
            if found:
                out.append(f"  {name} → {found[0].id} (the knowledge base has this very file)")
        if not out:
            return [f"Runs on: no knowledge-base family takes {' / '.join(bases) or 'this model'} — "
                    "say so; the nearest family's look will differ."]
        return ["Runs on (the image's base model, in the knowledge base):"] + list(dict.fromkeys(out))

    @staticmethod
    def _unknown_sampling(ctx: PipelineToolContext, recipe: ImageRecipe) -> list[str]:
        """A sampler or scheduler the record names that ComfyUI does not have (a Forge preset, a
        custom node's own sampler): said, with what to do — the recipe's default stands in."""
        spec = ((ctx.catalogue or {}).get("KSampler") or {}).get("input", {}).get("required", {})
        out = []
        for what, value, key in (("sampler", recipe.sampler, "sampler_name"), ("scheduler", recipe.scheduler, "scheduler")):
            known = (spec.get(key) or [[]])[0]
            if value and known and value not in known:
                out.append(f"  {what} {value!r} is not one ComfyUI has (a Forge preset or a custom node's own "
                           f"{what}): leave the recipe's default {what} and say the result may differ slightly")
        return out

    #: The port that swaps a recipe's model, and the folder its file lives in — a checkpoint for the
    #: SD lineage, a bare diffusion model for the DiT families (Anima, Krea 2, …).
    MODEL_PORTS = (("checkpoint", "checkpoints", "CheckpointLoaderSimple", "ckpt_name"),
                   ("unet_name", "diffusion_models", "UNETLoader", "unet_name"))

    @classmethod
    def _model_port(cls, ctx: PipelineToolContext, base: str) -> tuple[str, str, str, str]:
        """(port, folder, loader class, loader input) for a model of Civitai base `base`: the port
        the recipes of the family that runs it expose."""
        for fam in ctx.catalog.families.values():
            if not any(base in (m.get("bases") or []) for m in fam.lora.get("models") or []):
                continue
            for r in ctx.catalog.recipes_of(fam.id):
                for entry in cls.MODEL_PORTS:
                    if entry[0] in r.ports:
                        return entry
        return cls.MODEL_PORTS[0]

    @classmethod
    def _checkpoint_lines(cls, ctx: PipelineToolContext, checkpoints: list) -> list[str]:
        """The image's model file: what it is, the port it goes in, and the `models` entry that
        brings it — or that Comfy Cloud already has the very file."""
        lines = []
        for vid, hit, failed in checkpoints:
            if failed or hit is None:
                why = f"the Civitai lookup FAILED ({failed})" if failed else "not found on Civitai"
                lines.append(f"  model file: Civitai version {vid} — {why}; call reference_recipe again in a moment")
                continue
            port, folder, loader, field = cls._model_port(ctx, hit.base)
            cloud = set()
            if "captured" in str(ctx.catalogue_source):
                spec = (ctx.catalogue.get(loader) or {}).get("input") or {}
                cloud = {str(n).replace("\\", "/").rsplit("/", 1)[-1].lower()
                         for n in ((spec.get("required") or {}).get(field) or [[]])[0] if isinstance(n, str)}
            lines.append(f"  model file: {hit.name} — {hit.version} [{hit.base}], file {hit.file}")
            if hit.file.lower() in cloud:
                lines.append(f"     Comfy Cloud has this very file: set the recipe's `{port}` port to {hit.file!r}")
            else:
                entry = {"name": hit.file, "folder": folder, "base": hit.base, "url": hit.download_url}
                lines.append(f"     models entry: {entry} — and the recipe's `{port}` port set to {hit.file!r}")
            lines.append(f"     {hit.page}")
        return lines

    @staticmethod
    def _cloud_loras(ctx: PipelineToolContext) -> list[str]:
        if "captured" not in str(ctx.catalogue_source):
            return []
        spec = (ctx.catalogue.get("LoraLoaderModelOnly") or {}).get("input") or {}
        return [str(n) for n in ((spec.get("required") or {}).get("lora_name") or [[]])[0] if isinstance(n, str)]

    @staticmethod
    def _cloud_for_base(ctx: PipelineToolContext, cloud: list[str], base: str) -> list[str]:
        """Comfy Cloud LoRAs named for a model that takes LoRAs of `base`."""
        patterns = [p for fam in ctx.catalog.families.values() for m in fam.lora.get("models") or []
                    if base in (m.get("bases") or []) for p in m.get("cloud") or []]
        return [n for n in cloud if any(re.search(p, n.replace("\\", "/").rsplit("/", 1)[-1], re.I) for p in patterns)]

    @staticmethod
    def _similar(file: str, cloud: list[str]) -> list[str]:
        """Comfy Cloud LoRAs sharing at least two words with `file`."""
        def words(name: str) -> set[str]:
            stem = name.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
            stem = re.sub(r"([a-z])([A-Z])", r"\1 \2", stem)
            return {w for w in re.split(r"[^a-z0-9]+", stem.lower()) if len(w) > 2 and not w.isdigit()}
        mine = words(file)
        return [n for n in cloud if len(mine & words(n)) >= 2][:3]


def _stem(name: str) -> str:
    """A file or LoRA name without folder or extension, lower-cased: `gr33nXLP` for both
    `<lora:gr33nXLP:1>` and `models/loras/gr33nXLP.safetensors`."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    return (base.rsplit(".", 1)[0] if base.lower().endswith((".safetensors", ".pt", ".ckpt")) else base).lower()


def _stem_tail(name: str) -> str:
    return name.replace("\\", "/").rsplit("/", 1)[-1]


__all__ = ["ReferenceRecipeTool"]

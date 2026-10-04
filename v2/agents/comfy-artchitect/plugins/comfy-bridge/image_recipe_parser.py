"""ImageRecipeParser — an ImageRecipe from whatever generation record an image carries.

Three shapes (comfy_kb/lora_research.md §7):
  * a ComfyUI API GRAPH — a PNG's `prompt` chunk, Civitai's `meta.comfy` (a JSON string), or a
    Civitai-generator UserComment. Read by WHAT NODES DO, not by their ids: the model loaders, every
    node with a `lora_name` (core, rgthree's Power Lora Loader entries, Civitai's AIR URNs), the
    sampler and what feeds it (KSampler, or SamplerCustomAdvanced's guider / sampler / sigmas /
    noise), FluxGuidance, and the size of the latent the sampler starts from;
  * A1111 / Forge TEXT — the prompt, "Negative prompt: …", and one "Steps: …, Sampler: …" line
    carrying the rest ("Lora hashes", "Civitai resources", `<lora:name:weight>` in the prompt);
  * Civitai's own FIELDS — prompt, cfgScale, civitaiResources (version ids), additionalResources
    (file names), resources (names + hashes), hashes.

Pure: no I/O. A value wired from a primitive node is followed one hop.
"""

from __future__ import annotations

import json
import re

from image_recipe import ImageRecipe, ImageRecipeLora

_AIR = re.compile(r"urn:air:[^:]+:[^:]+:civitai:(\d+)@(\d+)")
_A1111_LORA = re.compile(r"<lora:([^:>]+):?([-\d.]*)[^>]*>")
_TEXT_KEYS = ("text", "text_g", "t5xxl", "clip_l", "string", "prompt", "value", "text_l")
_VALUE_KEYS = ("value", "int", "float", "seed", "noise_seed", "number", "Number")
_SAMPLERS = ("KSampler", "KSamplerAdvanced")

#: Sampler names other tools write -> ComfyUI's, AFTER normalising (lower case, "++" -> "pp",
#: spaces and dashes -> "_"): A1111 / Forge display names ("Euler a", "DPM2 a", "UniPC").
SAMPLER_ALIASES = {
    "euler_a": "euler_ancestral", "dpm2": "dpm_2", "dpm2_a": "dpm_2_ancestral", "dpmpp_2s_a": "dpmpp_2s_ancestral",
    "dpmpp_2s_a_karras": "dpmpp_2s_ancestral", "unipc": "uni_pc", "dpm_adaptive": "dpm_adaptive",
}
#: Schedule names other tools write (normalised) -> ComfyUI's schedulers. Also read as a sampler
#: name's suffix ("DPM++ 2M Karras", Civitai's "Euler_beta", "res_2s_beta57"); '' = no choice made.
SCHEDULE_ALIASES = {
    "karras": "karras", "exponential": "exponential", "sgm_uniform": "sgm_uniform", "simple": "simple",
    "normal": "normal", "beta57": "beta57", "beta": "beta", "ddim_uniform": "ddim_uniform",
    "kl_optimal": "kl_optimal", "linear_quadratic": "linear_quadratic", "bong_tangent": "bong_tangent",
    "align_your_steps": "", "automatic": "", "uniform": "sgm_uniform",
}
#: What a record writes when it has no sampler.
NO_SAMPLER = {"", "undefined", "none", "null", "default"}
_CUSTOM_SAMPLERS = ("SamplerCustomAdvanced", "SamplerCustom")


class ImageRecipeParser:
    # ------------------------------------------------------------------ entry points

    @classmethod
    def from_chunks(cls, chunks: dict[str, str]) -> ImageRecipe | None:
        """From an image file's own text (ImageMetadataReader)."""
        graph = cls._json(chunks.get("prompt", ""))
        if isinstance(graph, dict) and cls._is_graph(graph):
            return cls.from_graph(graph, "the ComfyUI graph embedded in the image")
        if chunks.get("parameters"):
            return cls.from_a1111(chunks["parameters"], "the A1111 parameters embedded in the image")
        comment = chunks.get("UserComment", "").strip()
        if comment.startswith("{"):
            data = cls._json(comment)
            if isinstance(data, dict) and cls._is_graph(data):
                return cls.from_graph(data, "the ComfyUI graph in the image's EXIF (Civitai generator)")
        if comment:
            return cls.from_a1111(comment, "the generation text in the image's EXIF (Civitai generator)")
        return None

    @classmethod
    def from_civitai_meta(cls, meta: dict) -> ImageRecipe | None:
        """From Civitai's record of the image (`/images?imageId=…&withMeta=true`)."""
        if not isinstance(meta, dict) or not meta:
            return None
        comfy = cls._json(meta["comfy"]) if isinstance(meta.get("comfy"), str) else meta.get("comfy")
        graph = (comfy or {}).get("prompt") if isinstance(comfy, dict) else None
        if isinstance(graph, dict) and cls._is_graph(graph):
            r = cls.from_graph(graph, "the ComfyUI graph Civitai kept with the image")
        else:
            r = ImageRecipe(source="Civitai's record of the image")
            r.prompt = str(meta.get("prompt") or "")
            r.negative = str(meta.get("negativePrompt") or "")
            r.steps = cls._int(meta.get("steps"))
            r.cfg = cls._float(meta.get("cfgScale"))
            r.sampler, r.scheduler = cls._comfy_sampler(str(meta.get("sampler") or ""),
                                                        str(meta.get("scheduler") or meta.get("Schedule type") or ""))
            r.seed = cls._int(meta.get("seed"))
            r.denoise = cls._float(meta.get("denoise") or meta.get("Denoising strength"))
            r.clip_skip = cls._skip(meta.get("clipSkip") or meta.get("Clip skip"))
            r.width, r.height = cls._size(meta.get("Size") or meta.get("resolution"))
            r.width = r.width or cls._int(meta.get("width"))
            r.height = r.height or cls._int(meta.get("height"))
            if meta.get("Model"):
                r.models.append(str(meta["Model"]))
            cls._prompt_loras(r)
        cls._civitai_resources(r, meta)
        return r

    @classmethod
    def from_graph(cls, graph: dict, source: str) -> ImageRecipe:
        g = {str(k): v for k, v in graph.items() if isinstance(v, dict) and v.get("class_type")}
        r = ImageRecipe(source=source, graph=g, classes=sorted({str(n["class_type"]) for n in g.values()}))
        for node in g.values():
            inputs = node.get("inputs") or {}
            for key in ("unet_name", "ckpt_name"):
                if isinstance(inputs.get(key), str):
                    cls._model(r, inputs[key])
            cls._graph_loras(r, inputs)
            if node["class_type"] == "FluxGuidance":
                r.guidance = cls._float(cls._literal(g, inputs.get("guidance")))
            if node["class_type"] == "CLIPSetLastLayer":
                r.clip_skip = cls._int(cls._literal(g, inputs.get("stop_at_clip_layer")))
        sampler = next((n for n in g.values() if n["class_type"] in _SAMPLERS), None)
        custom = next((n for n in g.values() if n["class_type"] in _CUSTOM_SAMPLERS), None)
        if sampler is not None:
            i = sampler["inputs"]
            r.steps = cls._int(cls._literal(g, i.get("steps")))
            r.cfg = cls._float(cls._literal(g, i.get("cfg")))
            r.sampler = str(cls._literal(g, i.get("sampler_name")) or "")
            r.scheduler = str(cls._literal(g, i.get("scheduler")) or "")
            r.seed = cls._int(cls._literal(g, i.get("seed", i.get("noise_seed"))))
            r.denoise = cls._float(cls._literal(g, i.get("denoise")))
            r.prompt = cls._text(g, i.get("positive"))
            r.negative = cls._text(g, i.get("negative"))
            latent = i.get("latent_image")
        elif custom is not None:
            i = custom["inputs"]
            guider = cls._node(g, i.get("guider")) or {}
            gi = guider.get("inputs") or {}
            r.prompt = cls._text(g, gi.get("conditioning", gi.get("positive")))
            r.negative = cls._text(g, gi.get("negative"))
            r.cfg = cls._float(cls._literal(g, gi.get("cfg")))
            si = (cls._node(g, i.get("sampler")) or {}).get("inputs") or {}
            r.sampler = str(cls._literal(g, si.get("sampler_name")) or "")
            sig = (cls._node(g, i.get("sigmas")) or {}).get("inputs") or {}
            r.steps = cls._int(cls._literal(g, sig.get("steps")))
            r.scheduler = str(cls._literal(g, sig.get("scheduler")) or "")
            r.denoise = cls._float(cls._literal(g, sig.get("denoise")))
            ni = (cls._node(g, i.get("noise")) or {}).get("inputs") or {}
            r.seed = cls._int(cls._literal(g, ni.get("noise_seed", ni.get("seed"))))
            latent = i.get("latent_image")
        else:
            latent = None
        size_node = cls._node(g, latent) or next(
            (n for n in g.values() if str(n["class_type"]).startswith("Empty") and "Latent" in str(n["class_type"])), None)
        if size_node is not None:
            si = size_node.get("inputs") or {}
            r.width = cls._int(cls._literal(g, si.get("width")))
            r.height = cls._int(cls._literal(g, si.get("height")))
        return r

    @classmethod
    def from_a1111(cls, text: str, source: str) -> ImageRecipe:
        r = ImageRecipe(source=source)
        resources = re.search(r"Civitai resources:\s*(\[.*?\])(?=,\s*\w[\w ]*:|\s*$)", text, re.S)
        if resources:
            for res in cls._json(resources.group(1)) or []:
                cls._civitai_resource(r, res)
            text = text[:resources.start()] + text[resources.end():]
        lines = text.strip().splitlines()
        settings_at = max((n for n, line in enumerate(lines) if re.match(r"\s*Steps:\s*\d", line)), default=None)
        settings = lines[settings_at] if settings_at is not None else ""
        body = "\n".join(lines[:settings_at] if settings_at is not None else lines)
        prompt, _, negative = body.partition("Negative prompt:")
        r.prompt, r.negative = prompt.strip(), negative.strip()
        fields = {k.strip(): v.strip().strip('"') for k, v in
                  re.findall(r'\s*([\w][\w \-/]*):\s*("(?:[^"\\]|\\.)*"|[^,]*)', settings)}
        r.steps = cls._int(fields.get("Steps"))
        r.sampler, r.scheduler = cls._comfy_sampler(fields.get("Sampler", ""), fields.get("Schedule type", ""))
        r.cfg = cls._float(fields.get("CFG scale"))
        r.guidance = cls._float(fields.get("Distilled CFG Scale"))
        r.seed = cls._int(fields.get("Seed"))
        r.denoise = cls._float(fields.get("Denoising strength"))
        r.clip_skip = cls._skip(fields.get("Clip skip"))
        r.width, r.height = cls._size(fields.get("Size"))
        if fields.get("Model"):
            r.models.append(fields["Model"])
        cls._prompt_loras(r)
        for pair in (fields.get("Lora hashes") or "").split(","):
            name, _, h = pair.partition(":")
            if name.strip() and h.strip():
                hit = next((lo for lo in r.loras if lo.name == name.strip()), None)
                if hit is None:
                    r.loras.append(ImageRecipeLora(name.strip(), hash=h.strip()))
                else:
                    hit.hash = h.strip()
        return r

    # ------------------------------------------------------------------ pieces

    @classmethod
    def _graph_loras(cls, r: ImageRecipe, inputs: dict) -> None:
        if isinstance(inputs.get("lora_name"), str):
            strength = inputs.get("strength_model", inputs.get("strength", inputs.get("strength_clip", 1.0)))
            cls._lora(r, inputs["lora_name"], strength)
        for value in inputs.values():  # rgthree Power Lora Loader: lora_1 = {on, lora, strength}
            if isinstance(value, dict) and isinstance(value.get("lora"), str) and value.get("on", True):
                cls._lora(r, value["lora"], value.get("strength", 1.0))

    @classmethod
    def _lora(cls, r: ImageRecipe, name: str, strength) -> None:
        air = _AIR.search(name)
        r.loras.append(ImageRecipeLora(name=name, strength=cls._float(strength) or 1.0,
                                       version_id=int(air.group(2)) if air else None))

    @classmethod
    def _model(cls, r: ImageRecipe, name: str) -> None:
        air = _AIR.search(name)
        if air:
            r.model_version_ids.append(int(air.group(2)))
        r.models.append(name)

    @classmethod
    def _prompt_loras(cls, r: ImageRecipe) -> None:
        for name, weight in _A1111_LORA.findall(r.prompt):
            r.loras.append(ImageRecipeLora(name=name, strength=cls._float(weight) or 1.0))

    @classmethod
    def _civitai_resources(cls, r: ImageRecipe, meta: dict) -> None:
        known = {lo.version_id for lo in r.loras if lo.version_id}
        for res in meta.get("civitaiResources") or []:
            if res.get("modelVersionId") not in known:
                cls._civitai_resource(r, res)
        named = {lo.name.replace("\\", "/").rsplit("/", 1)[-1] for lo in r.loras}
        for res in meta.get("additionalResources") or []:
            name = str(res.get("name") or "")
            if str(res.get("type") or "").lower() == "lora" and name.replace("\\", "/").rsplit("/", 1)[-1] not in named:
                r.loras.append(ImageRecipeLora(name=name, strength=cls._float(res.get("strength")) or 1.0))
        for res in meta.get("resources") or []:
            if str(res.get("type") or "").lower() == "lora":
                hit = next((lo for lo in r.loras if lo.name == res.get("name")), None)
                if hit is None:
                    r.loras.append(ImageRecipeLora(name=str(res.get("name") or ""),
                                                   strength=cls._float(res.get("weight")) or 1.0,
                                                   hash=str(res.get("hash") or "")))
                elif res.get("hash"):
                    hit.hash = str(res["hash"])
        for key, h in (meta.get("hashes") or {}).items():
            if str(key).startswith("lora:"):
                name = str(key)[5:]
                hit = next((lo for lo in r.loras if lo.name == name), None)
                if hit is not None and not hit.hash:
                    hit.hash = str(h)

    @classmethod
    def _civitai_resource(cls, r: ImageRecipe, res: dict) -> None:
        kind = str(res.get("type") or "").lower()
        vid = cls._int(res.get("modelVersionId"))
        if vid is None:
            return
        if kind in ("lora", "locon", "dora", "lycoris"):
            r.loras.append(ImageRecipeLora(name=str(res.get("modelVersionName") or res.get("modelName") or f"version {vid}"),
                                           strength=cls._float(res.get("weight")) or 1.0, version_id=vid))
        elif kind in ("checkpoint", "diffusionmodel", "unet"):
            r.model_version_ids.append(vid)

    # ------------------------------------------------------------------ graph walking

    @staticmethod
    def _is_graph(data: dict) -> bool:
        return any(isinstance(v, dict) and "class_type" in v for v in data.values())

    @staticmethod
    def _node(g: dict, link) -> dict | None:
        return g.get(str(link[0])) if isinstance(link, list) and link else None

    @classmethod
    def _literal(cls, g: dict, value):
        """The value, or — when it is wired — the primitive node's value one hop back."""
        node = cls._node(g, value)
        if node is None:
            return value
        inputs = node.get("inputs") or {}
        return next((inputs[k] for k in _VALUE_KEYS if k in inputs and not isinstance(inputs[k], list)), None)

    @classmethod
    def _text(cls, g: dict, link, depth: int = 0) -> str:
        """The prompt text a conditioning comes from: follow the links back to a text input."""
        node = cls._node(g, link)
        if node is None or depth > 6 or "ZeroOut" in str(node.get("class_type")):
            return ""  # a zeroed conditioning is the positive's shape with no text: no negative prompt
        inputs = node.get("inputs") or {}
        texts = [inputs[k] for k in _TEXT_KEYS if isinstance(inputs.get(k), str)]
        if texts:
            return max(texts, key=len)
        for key, value in inputs.items():
            if key in ("clip", "model", "vae") or not isinstance(value, list):
                continue
            found = cls._text(g, value, depth + 1)
            if found:
                return found
        return ""

    # ------------------------------------------------------------------ values

    @staticmethod
    def _json(text: str):
        try:
            return json.loads(text) if text else None
        except ValueError:
            return None

    @staticmethod
    def _int(value) -> int | None:
        try:
            return int(float(value)) if value not in (None, "") and not isinstance(value, (list, dict)) else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _float(value) -> float | None:
        try:
            return float(value) if value not in (None, "") and not isinstance(value, (list, dict)) else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _comfy_sampler(sampler: str, schedule: str) -> tuple[str, str]:
        """Another tool's sampler (and schedule) as ComfyUI's sampler_name and scheduler. Reads the
        shapes records come in: A1111 display names ("Euler a", "DPM++ 2M Karras"), Civitai's
        joined ones ("Euler_beta", "DPM++ 2M_sgm_uniform", "res_2s_beta57"), slash pairs
        ("exponential/res_2s_beta57", "linear/euler"). A name it cannot place is kept as written —
        reference_recipe holds the result against ComfyUI's own list and says so."""
        def norm(text: str) -> str:
            text = text.strip().lower().replace("++", "pp")
            return "_".join(t for t in text.replace("-", " ").replace("_", " ").split())

        sched = SCHEDULE_ALIASES.get(norm(schedule), norm(schedule)) if norm(schedule) else ""
        if norm(sampler) in NO_SAMPLER:
            return "", sched
        name = ""
        for part in [norm(x) for x in sampler.split("/") if norm(x)]:
            if part in SCHEDULE_ALIASES:
                sched = sched or SCHEDULE_ALIASES[part]  # a bare schedule beside the sampler
                continue
            for suffix in sorted(SCHEDULE_ALIASES, key=len, reverse=True):
                if part.endswith("_" + suffix) and len(part) > len(suffix) + 1:
                    part, sched = part[:-len(suffix) - 1], SCHEDULE_ALIASES[suffix] or sched
                    break
            name = SAMPLER_ALIASES.get(part, part)  # the sampler is the last name: "linear/euler"
        if not name or not name.replace("_", "").isalnum():
            return sampler.strip(), sched  # a preset ("[Forge] Flux Realistic"), not a sampler name
        return name, sched

    @classmethod
    def _skip(cls, value) -> int | None:
        """A1111's "Clip skip: N" (N >= 1) as ComfyUI's stop_at_clip_layer (-N)."""
        n = cls._int(value)
        return -n if n and n > 0 else None

    @staticmethod
    def _size(value) -> tuple[int | None, int | None]:
        m = re.match(r"\s*(\d+)\s*[x×]\s*(\d+)", str(value or ""))
        return (int(m.group(1)), int(m.group(2))) if m else (None, None)


__all__ = ["ImageRecipeParser"]

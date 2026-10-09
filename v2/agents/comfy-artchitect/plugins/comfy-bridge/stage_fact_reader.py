"""StageFactReader — what each stage of a design will do, read off its BUILT graph.

The approval card and the Stages panel show these, so neither can say something the run will not
do. The card used to carry the agent's own note ("a 9-second 1280×704 video") beside a design it
had since changed to 10 s at 1024×1536 — the note was prose, the graph was the truth. Here every
number comes from the graph the run submits: the recipe's ports name the node and input, and the
value is whatever the stage file holds now.

    model     the family's short name for the recipe ("LTX-2.3"), or "custom workflow"
    loras     "name @strength" for each LoRA the design added
    size      "1024×1536", or "" when the design does not set it (a size that follows a photo)
    frames / fps / seconds   a video's length, as far as the ports say it
    inputs    each input: the role, who fills it ("you" or the stage that makes it), whether the
              clip opens or ends on it exactly as given, and its media type
    review    the run stops after this stage so the person sees it
    prompt    the prompt the stage runs, when it has one
    aspect / images / cost_usd   a Seedream stage's frame, how many pictures, and what they are
              expected to cost (it has no graph; its settings are its ports)

Pure: a pipeline and its graphs in, facts out.
"""

from __future__ import annotations

from collections.abc import Callable

from knowledge_base_catalog import KnowledgeBaseCatalog
from output_size_estimator import OutputSizeEstimator
from pipeline import Pipeline, Stage
from seedream_stage import SeedreamStage
from stage_builder import StageBuildError, StageBuilder


class StageFactReader:
    def __init__(self, builder: StageBuilder, catalog: KnowledgeBaseCatalog, sizes: OutputSizeEstimator,
                 seedream_price: Callable[[int, int], float]) -> None:
        """:param seedream_price: (images, references) -> expected dollars of a Seedream job."""
        self._builder = builder
        self._catalog = catalog
        self._sizes = sizes
        self._seedream_price = seedream_price

    def facts(self, pipeline: Pipeline, graphs: dict[str, dict]) -> dict[str, dict]:
        """{stage name: facts}, in the design's order."""
        sizes = self._sizes.sizes(pipeline, graphs)
        return {s.name: self._stage(s, graphs.get(s.name) or {}, sizes.get(s.name)) for s in pipeline.stages}

    def _stage(self, stage: Stage, graph: dict, size: tuple[int, int] | None) -> dict:
        if stage.seedream:
            return self._seedream(stage)
        facts = {
            "model": "custom workflow",
            "loras": [_lora(lo) for lo in stage.loras],
            "size": f"{size[0]}×{size[1]}" if size else "",
            "frames": None,
            "fps": None,
            "seconds": None,
            "inputs": [],
            "review": stage.review,
            "prompt": "",
        }
        recipe = None
        if not stage.custom:
            try:
                recipe = self._builder.recipe_of(stage)
            except StageBuildError:
                recipe = None
        if recipe is not None:
            facts["model"] = self._catalog.families[recipe.family].display_name(recipe.id)
            port = _PortReader(recipe.ports, graph)
            frames, fps, duration = port.number("length"), port.number("fps"), port.number("duration")
            facts["frames"] = int(frames) if frames else None
            facts["fps"] = fps or None
            facts["seconds"] = (round(frames / fps, 1) if frames and fps else round(duration, 1) if duration else None)
            facts["prompt"] = port.text("prompt")
        for inp in stage.inputs:
            spec = (recipe.inputs.get(inp.name) or {}) if recipe is not None else {}
            facts["inputs"].append({
                "role": inp.role,
                "from": inp.producer[0] if inp.producer else "you",
                "output": inp.producer[1] if inp.producer else "",
                "frame": (spec.get("frame") or "") if not inp.producer else "",
                "type": str(spec.get("type") or ""),  # IMAGE / VIDEO / AUDIO — what the picker offers
            })
        return facts


    def _seedream(self, stage: Stage) -> dict:
        s = SeedreamStage(stage)
        try:
            cost = round(self._seedream_price(s.count, len(stage.inputs)), 4)
        except (KeyError, ValueError):
            cost = None  # no price configured: the card says nothing rather than a wrong number
        return {
            "model": "Seedream 5 Pro", "loras": [], "size": "", "frames": None, "fps": None, "seconds": None,
            "aspect": s.aspect_ratio, "images": s.count, "cost_usd": cost,
            "inputs": [{"role": i.role, "from": i.producer[0] if i.producer else "you",
                        "output": i.producer[1] if i.producer else "", "frame": "", "type": "IMAGE"}
                       for i in stage.inputs],
            "review": stage.review, "prompt": s.prompt,
        }


class _PortReader:
    """A recipe port's current value in a stage's graph."""

    def __init__(self, ports: dict, graph: dict) -> None:
        self._ports = ports
        self._graph = graph

    def _raw(self, name: str):
        spec = self._ports.get(name) or {}
        nid = spec.get("node") or (spec.get("nodes") or [None])[0]
        return ((self._graph.get(str(nid)) or {}).get("inputs") or {}).get(spec.get("input")) if nid else None

    def number(self, name: str) -> float | None:
        v = self._raw(name)
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    def text(self, name: str) -> str:
        v = self._raw(name)
        return v.strip() if isinstance(v, str) else ""


def _lora(lo) -> str:
    name = lo.name.replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".safetensors")
    return name + (f" @{lo.strength:g}" if lo.strength != 1.0 else "")


__all__ = ["StageFactReader"]

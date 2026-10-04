"""OutputSizeEstimator — roughly how big each stage's result is, from the design alone.

Read from the stage's own ports in its built graph: a super-resolution target (`sr_width` x
`sr_height`), else a generation size (`width` x `height`), and an upscale `scale` multiplies what
the stage it reads made. An edit whose size follows its input inherits the earlier stage's size.
None when the design does not say (the size of a file the person adds is unknown until it exists).
"""

from __future__ import annotations

from pipeline import Pipeline, Stage
from stage_builder import StageBuildError, StageBuilder

Size = tuple[int, int]


class OutputSizeEstimator:
    def __init__(self, builder: StageBuilder) -> None:
        self._builder = builder

    def sizes(self, pipeline: Pipeline, graphs: dict[str, dict]) -> dict[str, Size | None]:
        out: dict[str, Size | None] = {}
        for stage in pipeline.stages:
            out[stage.name] = self._size(stage, graphs.get(stage.name) or {}, out)
        return out

    def _size(self, stage: Stage, graph: dict, earlier: dict[str, Size | None]) -> Size | None:
        if stage.custom:
            return None
        try:
            ports = self._builder.recipe_of(stage).ports
        except StageBuildError:
            return None

        def value(name: str):
            spec = ports.get(name) or {}
            nid = spec.get("node") or (spec.get("nodes") or [None])[0]
            v = ((graph.get(str(nid)) or {}).get("inputs") or {}).get(spec.get("input")) if nid else None
            return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None

        for w, h in (("sr_width", "sr_height"), ("width", "height")):
            if value(w) and value(h):
                return int(value(w)), int(value(h))
        fed = next((earlier.get(i.producer[0]) for i in stage.inputs if i.producer), None)
        if fed is None:
            return None
        scale = value("scale")
        if scale:
            return int(fed[0] * scale), int(fed[1] * scale)
        # A REFERENCE EDIT keeps its first image's SHAPE at a pixel budget (FLUX.2's megapixels):
        # 1080x1350 in at 1 MP comes out about 894x1118.
        mp = value("megapixels")
        if mp:
            k = (mp * 1_000_000 / (fed[0] * fed[1])) ** 0.5
            return int(fed[0] * k), int(fed[1] * k)
        return fed


__all__ = ["OutputSizeEstimator"]

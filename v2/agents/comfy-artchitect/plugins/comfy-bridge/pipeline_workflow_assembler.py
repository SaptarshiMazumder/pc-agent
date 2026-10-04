"""PipelineWorkflowAssembler — a pipeline's stages as ONE ComfyUI workflow: the file a person downloads.

The agent runs a pipeline stage by stage (a review point stops between them, a changed stage is
re-run alone), so each stage is its own graph. A person wants one file: drag it into ComfyUI and
every stage is there, wired. This joins the stage graphs into that one graph:

  * node ids are renumbered across stages (each stage's recipe numbers from 1);
  * a hand-over becomes a WIRE: the later stage's file loader for an earlier stage's result is
    removed and the nodes that read it take the tensor the earlier stage saved (the input of its
    SaveImage / SaveVideo). The earlier stage's save node stays, so its result is still kept;
  * a hand-over whose types do not line up (a stage that saves frames, read as a video) keeps its
    loader, titled with what to load there — never a guessed conversion.

Pure: graphs in, one graph out. Writing it is the store's job.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import reference_slots
from api_graph import ApiGraph
from pipeline import Pipeline
from stage_builder import StageBuilder


@dataclass
class AssembledWorkflow:
    graph: dict
    #: (title, node ids) per stage, in order — the UI file's groups.
    groups: list[tuple[str, list[str]]] = field(default_factory=list)
    #: Hand-overs left as a loader, said in words ("stage b reads a's video from a file").
    unwired: list[str] = field(default_factory=list)


class PipelineWorkflowAssembler:
    def __init__(self, builder: StageBuilder, catalogue: dict) -> None:
        self._builder = builder
        self._catalogue = catalogue or {}

    def assemble(self, pipeline: Pipeline, graphs: dict[str, dict]) -> AssembledWorkflow:
        """`graphs`: each stage's built graph, by stage name."""
        out = AssembledWorkflow(graph={})
        ids: dict[str, dict[str, str]] = {}  # stage -> its node id -> combined id
        for n, stage in enumerate(pipeline.stages, start=1):
            graph = graphs.get(stage.name) or {}
            ids[stage.name] = {old: str(len(out.graph) + i + 1) for i, old in enumerate(graph)}
            for old, node in graph.items():
                new = ids[stage.name][old]
                out.graph[new] = {
                    **node,
                    "inputs": {k: self._remap(v, ids[stage.name]) for k, v in node["inputs"].items()},
                    "_meta": {**(node.get("_meta") or {}),
                              "title": f"{stage.name} · {(node.get('_meta') or {}).get('title') or node['class_type']}"},
                }
            out.groups.append((f"{n} · {stage.name}" + (f" — {stage.note}" if stage.note else ""),
                               list(ids[stage.name].values())))
        specs = {s.name: self._builder.output_specs(s) for s in pipeline.stages}
        for stage in pipeline.stages:
            for inp in stage.inputs:
                if inp.producer:
                    self._wire(out, ids, specs, stage.name, inp.role, inp.producer)
        return out

    # ------------------------------------------------------------------ hand-over

    def _wire(self, out: AssembledWorkflow, ids: dict, specs: dict, consumer: str, role: str,
              producer: tuple[str, str]) -> None:
        prod_stage, prod_output = producer
        spec = (specs.get(prod_stage) or {}).get(prod_output) or {}
        saver = ids[prod_stage].get(str(spec.get("node")))
        tensor = self._saved_tensor(out.graph, saver) if saver else None
        loaders = [nid for nid in ids[consumer].values()
                   if any(v == reference_slots.TOKEN + role for v in out.graph[nid]["inputs"].values())]
        for loader in loaders:
            kind = self._output_type(out.graph[loader]["class_type"], 0)
            used_slots = {v[1] for n in out.graph.values() for v in n["inputs"].values()
                          if ApiGraph.is_link(v) and v[0] == loader}
            if tensor is None or kind != tensor[1] or used_slots - {0}:
                out.graph[loader]["_meta"]["title"] = (f"{consumer} · LOAD {prod_stage}'s {prod_output} here "
                                                       f"(its saved file)")
                out.unwired.append(f"{consumer} reads {prod_stage}'s {prod_output} from a file (a loader is "
                                   "left for it)")
                continue
            for node in out.graph.values():
                for k, v in node["inputs"].items():
                    if ApiGraph.is_link(v) and v[0] == loader:
                        node["inputs"][k] = list(tensor[0])
            del out.graph[loader]
            for _, members in out.groups:
                if loader in members:
                    members.remove(loader)

    def _saved_tensor(self, graph: dict, saver: str) -> tuple[list, str] | None:
        """([node, slot], type) of what the save node writes — its one wired input."""
        node = graph.get(saver) or {}
        spec = self._declared(node.get("class_type", ""))
        for name, value in (node.get("inputs") or {}).items():
            if ApiGraph.is_link(value):
                kind = (spec.get(name) or [None])[0]
                if isinstance(kind, str):
                    return value, kind
        return None

    def _output_type(self, class_type: str, slot: int) -> str:
        outs = (self._catalogue.get(class_type) or {}).get("output") or []
        return str(outs[slot]) if slot < len(outs) else ""

    def _declared(self, class_type: str) -> dict:
        spec = (self._catalogue.get(class_type) or {}).get("input") or {}
        return {**(spec.get("required") or {}), **(spec.get("optional") or {})}

    @staticmethod
    def _remap(value, table: dict[str, str]):
        if ApiGraph.is_link(value) and str(value[0]) in table:
            return [table[str(value[0])], value[1]]
        return value


__all__ = ["AssembledWorkflow", "PipelineWorkflowAssembler"]

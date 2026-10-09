"""StageBuilder — a stage of a pipeline, as the API graph that runs it.

A recipe stage starts from the knowledge base's graph — the official template's wiring, already
checked — and changes only what a design is allowed to change:

  * PORTS: the recipe's named settings (prompt, size, length, steps, seed …). A port names the
    node input(s) it sets; it may list legal `options`; it may carry `same_value` inputs that must
    move with it (an image resize that must match the latent) and a `linked_with` input derived
    from it (`steps` moves a two-sampler hand-over point: end_at_step = steps × ratio).
  * INPUTS: each media input reads a reference slot; the stage's binding decides which role (a
    file the person adds, or an earlier stage's output — see pipeline.Stage).

A custom stage (no recipe) is a graph the agent wrote; this applies its bindings the same way.

Everything that is wrong is refused with the reason — an unknown port, a value not among a port's
options, a port that is wired rather than set, an input the recipe does not have.
"""

from __future__ import annotations

import copy

import reference_slots
import seedream_stage
from api_graph import ApiGraph
from knowledge_base_catalog import KnowledgeBaseCatalog
from pipeline import Stage
from recipe import Recipe
from stage_lora_splicer import StageLoraSplicer

#: The media types a loader's slot reads, for a custom stage's inputs.
LOADER_TYPES = {"LoadImage": "IMAGE", "LoadImageMask": "MASK", "LoadVideo": "VIDEO", "LoadAudio": "AUDIO"}


class StageBuildError(ValueError):
    """The stage cannot be built as asked; the message says why."""


class StageBuilder:
    def __init__(self, catalog: KnowledgeBaseCatalog) -> None:
        self._catalog = catalog

    def recipe_of(self, stage: Stage) -> Recipe:
        if stage.seedream:
            raise StageBuildError(f"stage {stage.name} is a Seedream image stage — it has no ComfyUI recipe")
        r = self._catalog.recipe(stage.family, stage.recipe)
        if r is None:
            fams = sorted(self._catalog.families)
            if stage.family not in self._catalog.families:
                raise StageBuildError(f"stage {stage.name}: no knowledge-base family '{stage.family}' "
                                      f"(families: {', '.join(fams)})")
            have = ", ".join(sorted(x.id for x in self._catalog.recipes_of(stage.family)))
            raise StageBuildError(f"stage {stage.name}: {stage.family} has no recipe '{stage.recipe}' "
                                  f"(it has: {have})")
        return r

    def build(self, stage: Stage, custom_graph: dict | None = None) -> dict:
        """The stage's API graph. `custom_graph` is the current graph of a custom stage."""
        if stage.seedream:
            raise StageBuildError(f"stage {stage.name} is a Seedream image stage — it has no ComfyUI graph")
        if stage.custom:
            if not custom_graph:
                raise StageBuildError(f"stage {stage.name} has no recipe and no graph")
            if stage.loras:
                raise StageBuildError(f"stage {stage.name} has no recipe: a hand-written graph wires its own "
                                      "LoRA nodes (LoraLoaderModelOnly after the model loader) — `loras` is for "
                                      "recipe stages")
            graph = copy.deepcopy(custom_graph)
            self._bind_custom(stage, graph)
            self._name_outputs(stage, graph)
            return graph
        recipe = self.recipe_of(stage)
        graph = copy.deepcopy(recipe.graph)
        for port, value in stage.ports.items():
            self.apply_port(recipe, graph, port, value, stage.name)
        self._bind_recipe(stage, recipe, graph)
        try:
            StageLoraSplicer.splice(stage.name, stage.family, self._catalog.families[stage.family].lora,
                                    stage.loras, graph, stage.declared_bases())
        except ValueError as e:
            raise StageBuildError(str(e)) from e
        self._name_outputs(stage, graph)
        return graph

    @staticmethod
    def _name_outputs(stage: Stage, graph: dict) -> None:
        """EVERY FILE A STAGE SAVES IS NAMED FOR THE STAGE: `<stage>/<stage>_00001_.png`. Recipes keep
        their template's prefix ("ComfyUI", "upscaled/ComfyUI", "video/joined"), and ComfyUI numbers
        each subfolder from 1 — but the chat keeps a download by its file name alone, so one stage's
        `ComfyUI_00001_.png` replaced another's, in the chat and in what was handed to the next stage.
        A stage with several savers adds the node id."""
        savers = [nid for nid, node in graph.items()
                  if isinstance(node, dict) and isinstance((node.get("inputs") or {}).get("filename_prefix"), str)]
        for nid in savers:
            name = stage.name if len(savers) == 1 else f"{stage.name}_{nid}"
            graph[nid]["inputs"]["filename_prefix"] = f"{stage.name}/{name}"

    # ------------------------------------------------------------------ ports

    @staticmethod
    def apply_port(recipe: Recipe, graph: dict, port: str, value, stage: str = "") -> list[str]:
        """Set one port on `graph`; returns 'node.input' of everything it changed."""
        spec = recipe.ports.get(port)
        if spec is None:
            raise StageBuildError(f"stage {stage}: recipe {recipe.id} has no port '{port}' "
                                  f"(ports: {', '.join(sorted(recipe.ports))})")
        options = spec.get("options")
        if options and value not in options:
            raise StageBuildError(f"stage {stage}: port '{port}' takes one of {options}, not {value!r}")
        changed = []
        targets = list(spec.get("nodes") or ([spec["node"]] if spec.get("node") else []))
        targets += [s["node"] for s in spec.get("same_value") or []]
        names = [spec.get("input")] * (len(targets) - len(spec.get("same_value") or [])) + \
                [s["input"] for s in spec.get("same_value") or []]
        for nid, name in zip(targets, names):
            changed.append(_set(graph, str(nid), str(name), value, stage, port))
        lw = spec.get("linked_with")
        if isinstance(lw, dict) and isinstance(value, (int, float)) and not isinstance(value, bool):
            derived = value * float(lw.get("ratio", 1.0))
            derived = int(round(derived)) if isinstance(value, int) else derived
            for n_key, i_key in (("node", "input"), ("node2", "input2")):
                if lw.get(n_key):
                    changed.append(_set(graph, str(lw[n_key]), str(lw[i_key]), derived, stage, port))
        return changed

    # ------------------------------------------------------------------ inputs

    def _bind_recipe(self, stage: Stage, recipe: Recipe, graph: dict) -> None:
        declared = recipe.inputs
        for i in stage.inputs:
            spec = declared.get(i.name)
            if spec is None:
                raise StageBuildError(f"stage {stage.name}: recipe {recipe.id} has no input '{i.name}' "
                                      f"(inputs: {', '.join(sorted(declared)) or 'none'})")
            node = graph.get(str(spec["node"]))
            if node is None:
                raise StageBuildError(f"stage {stage.name}: recipe {recipe.id} input '{i.name}' names a missing node")
            node["inputs"][spec["input"]] = reference_slots.TOKEN + i.role

    @staticmethod
    def _bind_custom(stage: Stage, graph: dict) -> None:
        """In a custom graph an input's NAME is the slot role the agent wrote; rebind it."""
        g = ApiGraph(graph)
        for i in stage.inputs:
            hits = [(nid, f) for nid in g.nodes for f, v in g.inputs(nid).items()
                    if v == reference_slots.TOKEN + i.name]
            if not hits:
                raise StageBuildError(f"stage {stage.name}: no loader reads '{reference_slots.TOKEN}{i.name}'")
            for nid, f in hits:
                graph[nid]["inputs"][f] = reference_slots.TOKEN + i.role

    # ------------------------------------------------------------------ what a stage takes and makes

    def input_types(self, stage: Stage, graph: dict | None = None) -> dict[str, str]:
        """{input name: media type} the stage reads."""
        if stage.seedream:  # every input is a reference picture, named as the design binds it
            return {i.name: "IMAGE" for i in stage.inputs}
        if not stage.custom:
            return {n: str(s.get("type") or "") for n, s in self.recipe_of(stage).inputs.items()}
        out = {}
        g = ApiGraph(graph or {})
        for nid in g.nodes:
            for f, v in g.inputs(nid).items():
                role = reference_slots.role_of(v)
                if role is not None:
                    out[role] = LOADER_TYPES.get(g.class_of(nid), "")
        return out

    def output_specs(self, stage: Stage) -> dict[str, dict]:
        """{output name: {node, type}} — which node of the stage's graph saves each result."""
        if stage.seedream:
            return dict(seedream_stage.OUTPUTS)
        return dict(stage.outputs if stage.custom else self.recipe_of(stage).outputs)

    def output_types(self, stage: Stage) -> dict[str, str]:
        """{output name: media type} the stage produces."""
        return {n: str(s.get("type") or "") for n, s in self.output_specs(stage).items()}


def _set(graph: dict, nid: str, name: str, value, stage: str, port: str) -> str:
    node = graph.get(nid)
    if node is None:
        raise StageBuildError(f"stage {stage}: port '{port}' points at node {nid}, which is not in the graph")
    current = node["inputs"].get(name)
    if ApiGraph.is_link(current):
        raise StageBuildError(f"stage {stage}: port '{port}' sets {node['class_type']}.{name}, which is wired "
                              f"from node {current[0]} — set that node instead")
    node["inputs"][name] = value
    return f"{nid}.{name}"


__all__ = ["LOADER_TYPES", "StageBuildError", "StageBuilder"]

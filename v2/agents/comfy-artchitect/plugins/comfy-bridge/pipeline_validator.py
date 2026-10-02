"""PipelineValidator — is the whole design right, before anything is downloaded or rented? Layer 3.

Every stage gets layers 1 and 2 (GraphStructuralValidator: will ComfyUI accept it;
FamilyRuleValidator: is it right for its models). Then the pipeline as a whole:

  * names, sources and ORDER — a stage reads only from stages before it
  * every media input of every stage is BOUND — to a file the person adds or an earlier stage's
    output; an unbound recipe input would read a slot nobody fills
  * a stage-fed input reads an output its producer HAS, of the TYPE it takes (an IMAGE stage cannot
    feed a VIDEO input)
  * the ComfyUI each recipe needs is not newer than the box's
  * the model files, counted once across stages, against the rented box's DISK — a note for setup,
    never a design error
  * node packs a stage needs are named — Phase 2 installs them
  * how the job is SPLIT (PipelineDesignChecks) — `?` findings about the design as a whole

`holds` is True only with no errors anywhere. Warnings are listed to be answered. Nothing here
needs a GPU: the node list comes from NodeRegistryCache.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from family_rule_validator import FamilyRuleValidator, RuleReport
from graph_structural_validator import GraphStructuralValidator, StructuralReport
from knowledge_base_catalog import KnowledgeBaseCatalog
from pipeline import Pipeline, Stage
from pipeline_design_checks import PipelineDesignChecks
from recipe import version_tuple
from stage_builder import StageBuildError, StageBuilder

#: The rented box's disk (vast InstanceSettings.disk_gb) less room for ComfyUI's own files,
#: outputs and temporary downloads.
DISK_GB = 60.0
DISK_HEADROOM_GB = 8.0


@dataclass
class StageVerdict:
    stage: str
    structure: StructuralReport | None = None
    rules: RuleReport | None = None
    problems: list[str] = field(default_factory=list)  # build / binding errors for this stage
    unjudged: list[str] = field(default_factory=list)  # what the node list here is too old to judge

    @property
    def errors(self) -> list[str]:
        out = list(self.problems)
        s = self.structure
        if s is not None:
            out += (s.unknown_nodes if not self.unjudged else []) + s.bad_links + s.bad_inputs + s.bad_enums + s.bad_values
            if s.no_output_node:
                out.append("no output node: ComfyUI refuses a graph that saves nothing")
        if self.rules is not None:
            out += [f.render() for f in self.rules.errors]
        return out

    @property
    def warnings(self) -> list[str]:
        return [f.render() for f in self.rules.warnings] if self.rules is not None else []


@dataclass
class PipelineReport:
    stages: list[StageVerdict]
    problems: list[str]  # pipeline-level errors
    warnings: list[str]
    user_inputs: dict[str, str]  # role -> what it is for: what the person must add
    files: dict[str, int | None]  # model file -> bytes (None when unknown)
    packs: list[str]
    catalogue_source: str
    #: Facts for Phase 2 (disk) — never design questions: the design does not bend to the machine.
    setup_notes: list[str] = field(default_factory=list)

    @property
    def holds(self) -> bool:
        return not self.problems and not any(v.errors for v in self.stages)

    @property
    def questions(self) -> list[str]:
        """Every `?` finding, in the order render_report numbers them (q1, q2, …). Each is answered
        before the card: fixed in the design, or one line on why it is right for this job."""
        return [w for v in self.stages for w in v.warnings] + list(self.warnings)

    @property
    def download_gb(self) -> float:
        return sum(b or 0 for b in self.files.values()) / 1e9


class PipelineValidator:
    def __init__(self, catalog: KnowledgeBaseCatalog, builder: StageBuilder, rules: FamilyRuleValidator,
                 design: PipelineDesignChecks, catalogue: dict, catalogue_source: str,
                 catalogue_version: str, comfyui_version: str = "") -> None:
        self._catalog = catalog
        self._builder = builder
        self._rules = rules
        self._design = design
        self._structure = GraphStructuralValidator(catalogue, live=False)
        self._source = catalogue_source
        self._listed = catalogue_version
        self._version = comfyui_version

    def check(self, pipeline: Pipeline, graphs: dict[str, dict]) -> PipelineReport:
        """:param graphs: each stage's built graph, by stage name (from PipelineStore)."""
        problems = list(pipeline.order_problems())
        warnings: list[str] = []
        verdicts, user_inputs, files, packs = [], {}, {}, []
        if not pipeline.stages:
            problems.append("the pipeline has no stages")
        for stage in pipeline.stages:
            v = StageVerdict(stage.name)
            verdicts.append(v)
            graph = graphs.get(stage.name) or {}
            try:
                recipe = None if stage.custom else self._builder.recipe_of(stage)
                v.problems += self._binding_problems(pipeline, stage, graph)
            except StageBuildError as e:
                v.problems.append(str(e))
                continue
            v.structure = self._structure.check(graph)
            v.unjudged = self._not_judged_here(recipe, v.structure)
            v.rules = self._rules.check(graph, comfyui_version=self._version, recipe=recipe)
            if recipe is not None:
                if recipe.meta.get("blocked"):
                    v.problems.append(f"recipe {recipe.family}/{recipe.id} cannot run here yet: {recipe.meta['blocked']}")
                if self._version and not recipe.runs_on(self._version):
                    v.problems.append(f"recipe {recipe.family}/{recipe.id} needs ComfyUI "
                                      f"{recipe.meta.get('min_comfyui')}; the box runs {self._version}")
                packs += recipe.pack_links
            for i in stage.inputs:
                if not i.producer:
                    user_inputs.setdefault(i.role, f"stage {stage.name}: {i.name}")
            for name in self._model_files(graph):
                rec = self._catalog.describe_file(name)
                files.setdefault(name, (rec[1].get("bytes") if rec else None))
            unknown = self._catalog.unknown_model_files(graph)
            if unknown:
                warnings.append(f"stage {stage.name}: model files no knowledge-base family describes — "
                                f"their wiring is unchecked by the family rules: {', '.join(unknown)}")
        warnings += self._design.check(pipeline, graphs)
        total = sum(b or 0 for b in files.values()) / 1e9
        budget = DISK_GB - DISK_HEADROOM_GB
        setup_notes = []
        if total > budget:
            # NOT A DESIGN ERROR. The design is the best route for the job; the disk is the rented
            # machine's, and Phase 2 deals with it. An error here made the agent drop the identity
            # still that the job depended on, to fit 52 GB.
            setup_notes.append(f"the models come to {total:.1f} GB, more than the {budget:.0f} GB the rented GPU "
                               "keeps for models: setting up needs a bigger disk or the stages' models one "
                               "at a time — a Phase 2 matter. Never change the design to fit it.")
        unsized = [n for n, b in files.items() if b is None]
        if unsized:
            setup_notes.append(f"size unknown (not counted in the disk check): {', '.join(sorted(unsized))}")
        return PipelineReport(verdicts, problems, warnings, user_inputs, files, sorted(set(packs)), self._source,
                              setup_notes)

    def _not_judged_here(self, recipe, structure: StructuralReport) -> list[str]:
        """Classes the node list here cannot know, though the box will have them: a recipe's custom
        node PACKS (installed in setup) or a NEWER ComfyUI than the list. Not judged here — the box
        judges them in setup; everything else about the stage still is. Only for a recipe, whose
        classes come from the publisher's own workflow: in a hand-written graph an unknown class is
        usually a typo, and stays an error."""
        if recipe is None or not structure.raw_unknown:
            return []
        unknown = ", ".join(sorted(set(structure.raw_unknown)))
        if recipe.packs:
            return [f"not judged here: {unknown} — from the recipe's node pack(s), installed and checked in setup"]
        if recipe.min_comfyui <= version_tuple(self._listed or "0"):
            return []
        if self._version and not recipe.runs_on(self._version):
            return []  # the box is too old: reported as a problem with the recipe's version
        return [f"not judged here: {unknown} — this recipe needs ComfyUI "
                f"{recipe.meta.get('min_comfyui')}, newer than the node list here ({self._listed}); "
                "the box checks them when it is set up"]

    # ------------------------------------------------------------------ bindings

    def _binding_problems(self, pipeline: Pipeline, stage: Stage, graph: dict) -> list[str]:
        out = []
        earlier = {s.name for s in pipeline.stages[:pipeline.stages.index(stage)]}
        for i in stage.inputs:
            if not i.producer and (i.role in earlier or any(i.role.startswith(n + "_") for n in earlier)):
                out.append(f"stage {stage.name}.{i.name} reads user:{i.role}, which is an earlier stage's result, not a "
                           f"file the person adds — bind stage:<that stage>.<output>; the run hands it over itself")
        takes = self._builder.input_types(stage, graph)
        bound = {i.name for i in stage.inputs}
        for name in sorted(set(takes) - bound):
            out.append(f"stage {stage.name}: input '{name}' ({takes[name] or 'media'}) is not bound — "
                       "bind it to user:<role> or stage:<name>.<output>")
        for i in stage.inputs:
            if i.name not in takes:
                out.append(f"stage {stage.name}: binds '{i.name}', which it does not take "
                           f"(it takes: {', '.join(sorted(takes)) or 'nothing'})")
                continue
            prod = i.producer
            if not prod:
                continue
            producer = pipeline.stage(prod[0])
            if producer is None:
                continue  # reported by order_problems
            try:
                makes = self._builder.output_types(producer)
            except StageBuildError as e:
                out.append(str(e))
                continue
            if prod[1] not in makes:
                out.append(f"stage {stage.name}.{i.name} reads {prod[0]}.{prod[1]}, but {prod[0]} produces "
                           f"{', '.join(f'{n} ({t})' for n, t in makes.items()) or 'nothing'}")
            elif takes[i.name] and makes[prod[1]] and takes[i.name] != makes[prod[1]]:
                out.append(f"stage {stage.name}.{i.name} takes {takes[i.name]} but {prod[0]}.{prod[1]} is "
                           f"{makes[prod[1]]}")
        return out

    @staticmethod
    def _model_files(graph: dict) -> list[str]:
        exts = (".safetensors", ".gguf", ".ckpt", ".pt", ".pth", ".sft", ".onnx")
        return sorted({v for n in graph.values() for v in (n.get("inputs") or {}).values()
                       if isinstance(v, str) and v.lower().endswith(exts)})


def render_report(report: PipelineReport) -> str:
    """The report for a tool result."""
    lines = []
    head = "the design HOLDS" if report.holds else "the design does NOT hold"
    lines.append(f"{head} — {len(report.stages)} stage(s), checked without a GPU against the {report.catalogue_source}.")
    for p in report.problems:
        lines.append(f"  ✗ {p}")
    n = 0
    for v in report.stages:
        fams = ", ".join(v.rules.families) if v.rules and v.rules.families else "no known family"
        lines.append(f"stage {v.stage} [{fams}]: " + ("OK" if not v.errors else f"{len(v.errors)} error(s)"))
        lines += [f"  ✗ {e}" for e in v.errors]
        lines += [f"  · {u}" for u in v.unjudged]  # information, not a question
        for w in v.warnings:
            n += 1
            lines.append(f"  ? [q{n}] {w}")
    if report.warnings:
        lines.append("pipeline:")
        for w in report.warnings:
            n += 1
            lines.append(f"  ? [q{n}] {w}")
    if report.user_inputs:
        lines.append("the person adds (References panel): " +
                     "; ".join(f"@{r} ({w})" for r, w in sorted(report.user_inputs.items())))
    lines.append(f"downloads: {len(report.files)} model file(s), {report.download_gb:.1f} GB"
                 + (f"; node packs: {', '.join(report.packs)}" if report.packs else ""))
    lines += [f"for setup: {note}" for note in report.setup_notes]
    if report.questions:
        lines.append("? = answer each: fix it in the design, or give one line on why it is right for this job "
                     "(pipeline_present takes those lines as `answers`, by number).")
    return "\n".join(lines)


__all__ = ["DISK_GB", "DISK_HEADROOM_GB", "PipelineReport", "PipelineValidator", "StageVerdict", "render_report"]

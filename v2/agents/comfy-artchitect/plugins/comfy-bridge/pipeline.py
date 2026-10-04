"""Pipeline — a job's design: one or more STAGES, each a workflow, each feeding the next.

A request rarely maps to one graph. "A video of this person deadlifting" is a character sheet from
their photo, a keyframe from the sheet, and a clip from the keyframe — three workflows, three
models, each worth looking at before the next spends a GPU's time. A Pipeline holds that shape:

    Stage        one workflow: built from a knowledge-base recipe (family + recipe id + the values
                 of its ports), or written node by node when no recipe covers it (`custom`)
    StageInput   one media input of a stage, and where it comes from:
                     user:<role>             a file the person adds in the References panel
                     stage:<name>.<output>   what an earlier stage produced

THE HANDOFF IS A REFERENCE SLOT. Every media input of a stage's graph is a loader reading a slot
token (`@role`, reference_slots). A user input's role is the role the person fills; a stage input's
role is the producer's (`<stage>_<output>`), and the runtime fills that slot with the producer's
output file. So comfy_run, the References panel and the Library treat a stage like any workflow, and
the agent never carries a filename from one stage to the next.

Pure: no I/O. PipelineStore reads and writes it; StageBuilder turns a stage into a graph.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from stage_lora import StageLora
from stage_model_file import StageModelFile

USER = "user"
STAGE = "stage"
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
_SOURCE = re.compile(r"^(user):([a-z][a-z0-9_-]{0,31})$|^(stage):([a-z][a-z0-9_]{0,23})\.([a-z][a-z0-9_]{0,23})$")


@dataclass(frozen=True)
class StageInput:
    name: str  # the input's name in the recipe ("start_image") or the slot role in a custom graph
    source: str  # "user:<role>" | "stage:<stage>.<output>"

    @property
    def kind(self) -> str:
        return USER if self.source.startswith(f"{USER}:") else STAGE

    @property
    def role(self) -> str:
        """The reference-slot role the stage's graph reads this input through."""
        m = _SOURCE.match(self.source)
        if not m:
            raise ValueError(f"'{self.source}' is not user:<role> or stage:<name>.<output>")
        return m.group(2) if m.group(1) else slot_role(m.group(4), m.group(5))

    @property
    def producer(self) -> tuple[str, str] | None:
        """(stage, output) for a stage-fed input; None for a user's file."""
        m = _SOURCE.match(self.source)
        return (m.group(4), m.group(5)) if m and m.group(3) else None


@dataclass
class Stage:
    name: str
    family: str = ""  # knowledge-base family id; "" for a custom stage
    recipe: str = ""  # recipe id within the family; "" for a custom stage
    ports: dict = field(default_factory=dict)  # port name -> value the design set
    inputs: list[StageInput] = field(default_factory=list)
    outputs: dict = field(default_factory=dict)  # custom stages only: {name: {node, type}}
    review: bool = False  # Phase 3 stops after this stage and shows its output
    note: str = ""
    #: LoRAs on top of the recipe's model, applied in order (StageBuilder splices them in).
    loras: list[StageLora] = field(default_factory=list)
    #: Model files a port swaps in that the knowledge base does not list (a Pony checkpoint), with
    #: what they are and where they come from.
    models: list[StageModelFile] = field(default_factory=list)

    @property
    def custom(self) -> bool:
        return not self.recipe

    def input(self, name: str) -> StageInput | None:
        return next((i for i in self.inputs if i.name == name), None)

    def sourced_files(self) -> dict[str, dict]:
        """{bare file name: {filename, url, kind, base}} for every file the stage brings its own
        source for — its LoRAs and its model files — the way setup imports them. `url` may be ''
        (Comfy Cloud has it, or nothing says where it comes from)."""
        out = {}
        for lo in self.loras:
            out[lo.name.replace("\\", "/").rsplit("/", 1)[-1]] = {
                "filename": lo.name.replace("\\", "/"), "url": lo.url, "kind": "loras", "base": lo.base}
        for m in self.models:
            out[m.file] = {"filename": m.name.replace("\\", "/"), "url": m.url, "kind": m.folder, "base": m.base}
        return out

    def declared_bases(self) -> dict[str, str]:
        """{bare file name: Civitai base} for the model files the stage declares a base for."""
        return {m.file: m.base for m in self.models if m.base}


@dataclass
class Pipeline:
    name: str
    stages: list[Stage] = field(default_factory=list)
    #: The size the person needs the final result at ("3840x2160"); "" when they named none.
    deliver_size: str = ""

    def stage(self, name: str) -> Stage | None:
        return next((s for s in self.stages if s.name == name), None)

    def order_problems(self) -> list[str]:
        """Names, sources and order: a stage reads only from stages BEFORE it."""
        out, seen = [], set()
        for s in self.stages:
            if not _NAME.match(s.name):
                out.append(f"stage '{s.name}': a name is lowercase letters, digits and _ (max 24), starting with a letter")
            if s.name in seen:
                out.append(f"two stages are named '{s.name}'")
            for i in s.inputs:
                if not _SOURCE.match(i.source):
                    out.append(f"stage {s.name}.{i.name}: '{i.source}' is not user:<role> or stage:<name>.<output>")
                    continue
                prod = i.producer
                if prod and prod[0] not in seen:
                    out.append(f"stage {s.name}.{i.name} reads '{prod[0]}', which is not an earlier stage")
            seen.add(s.name)
        return out

    def consumers_of(self, stage: str) -> list[tuple[Stage, StageInput]]:
        return [(s, i) for s in self.stages for i in s.inputs if i.producer and i.producer[0] == stage]

    def to_dict(self) -> dict:
        return {
            "format": "comfy-penguin-pipeline",
            "version": 1,
            "name": self.name,
            "deliver_size": self.deliver_size,
            "stages": [
                {
                    "name": s.name, "family": s.family, "recipe": s.recipe, "ports": s.ports,
                    "inputs": {i.name: i.source for i in s.inputs}, "outputs": s.outputs,
                    "review": s.review, "note": s.note,
                    **({"loras": [lo.to_dict() for lo in s.loras]} if s.loras else {}),
                    **({"models": [m.to_dict() for m in s.models]} if s.models else {}),
                }
                for s in self.stages
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Pipeline":
        if not isinstance(data, dict) or data.get("format") != "comfy-penguin-pipeline":
            raise ValueError("not a comfy-penguin-pipeline")
        stages = []
        for raw in data.get("stages") or []:
            stages.append(Stage(
                name=str(raw.get("name") or ""), family=str(raw.get("family") or ""),
                recipe=str(raw.get("recipe") or ""), ports=dict(raw.get("ports") or {}),
                inputs=[StageInput(str(k), str(v)) for k, v in (raw.get("inputs") or {}).items()],
                outputs=dict(raw.get("outputs") or {}), review=bool(raw.get("review")),
                note=str(raw.get("note") or ""),
                loras=[StageLora.from_dict(lo) for lo in raw.get("loras") or []],
                models=[StageModelFile.from_dict(m) for m in raw.get("models") or []],
            ))
        return cls(name=str(data.get("name") or ""), stages=stages, deliver_size=str(data.get("deliver_size") or ""))


def slot_role(stage: str, output: str) -> str:
    """The slot a stage's output fills for the stages that read it: `<stage>_<output>`, ≤32."""
    return f"{stage}_{output}"[:32]


__all__ = ["STAGE", "USER", "Pipeline", "Stage", "StageInput", "slot_role"]

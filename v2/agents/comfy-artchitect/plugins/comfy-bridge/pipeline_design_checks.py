"""PipelineDesignChecks — judgements about how a pipeline is SPLIT, which no single stage can see.

Each stage can be correct on its own and the job still be designed badly: a video made straight from
the person's own photo with nothing they approve first, the same file wired into two inputs, a review
point with nothing after it. These are `?` findings — the agent fixes the design or says in one line
why it is right for this job — never errors, because a plain text-to-video job legitimately has one
stage and no review.
"""

from __future__ import annotations

from collections import defaultdict

import re

from knowledge_base_catalog import KnowledgeBaseCatalog
from output_size_estimator import OutputSizeEstimator
from pipeline import Pipeline, Stage
from stage_builder import StageBuildError, StageBuilder

#: Loading and saving: in every graph, so they say nothing about what a stage DOES.
_PLUMBING = frozenset({"LoadImage", "LoadVideo", "LoadAudio", "SaveImage", "SaveVideo", "SaveAudio", "PreviewImage",
                       "GetVideoComponents", "CreateVideo", "VAELoader", "CLIPLoader", "UNETLoader", "VAEDecode",
                       "VAEEncode", "CheckpointLoaderSimple", "PrimitiveString", "PrimitiveInt", "PrimitiveFloat"})
_STILL = "IMAGE"
_MOVING = "VIDEO"


class PipelineDesignChecks:
    def __init__(self, builder: StageBuilder, sizes: OutputSizeEstimator, catalog: KnowledgeBaseCatalog) -> None:
        self._builder = builder
        self._sizes = sizes
        self._catalog = catalog

    def check(self, pipeline: Pipeline, graphs: dict[str, dict]) -> list[str]:
        out: list[str] = []
        for stage in pipeline.stages:
            try:
                takes = self._builder.input_types(stage, graphs.get(stage.name))
                makes = self._builder.output_types(stage)
            except StageBuildError:
                continue  # the stage's own verdict already says why
            out += self._same_file_twice(stage, graphs.get(stage.name) or {})
            out += self._video_from_raw_reference(stage, takes, makes, self._frames(stage))
            out += self._raw_into_prepared(stage, self._prepared(stage))
            out += self._map_into_raw(pipeline, stage)
            out += self._hand_written_but_covered(stage, graphs.get(stage.name) or {})
        out += self._review_with_nothing_after(pipeline)
        out += self._made_but_never_read(pipeline)
        out += self._short_of_delivery(pipeline, graphs)
        out += self._prompts_not_written(pipeline, graphs)
        return out

    def _prompts_not_written(self, pipeline: Pipeline, graphs: dict[str, dict]) -> list[str]:
        """A prompt still the recipe's EXAMPLE (the comic-book city of the template, not this job), and
        stages of one recipe given the same prompt — five 'angles' that all ask for one view make the
        same picture five times."""
        out, seen = [], {}
        for stage in pipeline.stages:
            if stage.custom:
                continue
            try:
                recipe = self._builder.recipe_of(stage)
            except StageBuildError:
                continue
            spec = recipe.ports.get("prompt") or {}
            nid = spec.get("node") or (spec.get("nodes") or [None])[0]
            if not nid:
                continue
            mine = ((graphs.get(stage.name) or {}).get(str(nid)) or {}).get("inputs", {}).get(spec.get("input"))
            example = (recipe.graph.get(str(nid)) or {}).get("inputs", {}).get(spec.get("input"))
            if not isinstance(mine, str) or not mine.strip():
                continue
            if mine == example:
                out.append(f"stage {stage.name}: the prompt is still the recipe's example — write this job's prompt "
                           "(stage_set prompt).")
            key = (stage.family, stage.recipe, mine.strip())
            if key in seen:
                out.append(f"stages {seen[key]} and {stage.name} run the same recipe with the same prompt — they make "
                           "the same result; give each its own prompt.")
            else:
                seen[key] = stage.name
        return out

    def _short_of_delivery(self, pipeline: Pipeline, graphs: dict[str, dict]) -> list[str]:
        """The person named a size (4K, a 1080x1920 reel) and the last stage makes less."""
        m = re.fullmatch(r"\s*(\d+)\s*x\s*(\d+)\s*", pipeline.deliver_size or "")
        if not m or not pipeline.stages:
            return []
        need = max(int(m.group(1)), int(m.group(2)))
        got = self._sizes.sizes(pipeline, graphs).get(pipeline.stages[-1].name)
        if got is None:
            # NOTHING SETS THE SIZE: the last stage's canvas follows an input image (a reference
            # editor without a size port), so the named size is a hope. Said at design time, where
            # it can still be fixed, rather than discovered in the result.
            return [f"the person needs {pipeline.deliver_size}, but no stage sets the size: the last stage "
                    f"({pipeline.stages[-1].name}) takes its canvas from its input image, so the result has that "
                    "image's shape. Start from a stage that renders at the exact size (a text-to-image stage "
                    "with width/height) and edit into it, or end with a stage that sets the size."]
        if max(got) >= need * 0.95:
            return []
        return [f"the last stage ({pipeline.stages[-1].name}) makes about {got[0]}x{got[1]}, but the person needs "
                f"{pipeline.deliver_size}. End with an upscale stage that reaches it (kb_lookup task upscale-image or "
                "upscale-video), or set the size the model can make."]

    def deliverables(self, pipeline: Pipeline) -> list[Stage]:
        """The stages whose results the person ends up with: those no later stage reads."""
        read = {i.producer[0] for s in pipeline.stages for i in s.inputs if i.producer}
        return [s for s in pipeline.stages if s.name not in read]

    def _made_but_never_read(self, pipeline: Pipeline) -> list[str]:
        """Stages whose output no later stage reads end the design as files of their own. Several
        CLIPS ending that way are separate videos, not one — the commonest miss when the person
        asked for one continuous video. Any other unread stage before the last is usually a still
        meant to drive a later stage and left unwired."""
        if len(pipeline.stages) < 2:
            return []
        ends = self.deliverables(pipeline)
        clips = [s for s in ends if _MOVING in self._types(s)]
        out = []
        if len(clips) > 1:
            out.append(f"{len(clips)} clips end the design as SEPARATE files ({', '.join(s.name for s in clips)}). "
                       "If the person asked for one video, add a join stage that reads them in order (kb_lookup "
                       "task join-clips; side by side: stack-clips). Keep them separate only if that is what they asked.")
        out += [f"stage {s.name} makes something no later stage reads. Wire it into the stage it was made for "
                "(stage:<name>.<output>) — or, if the person gets it as a deliverable of its own, say so."
                for s in ends if s is not pipeline.stages[-1] and s not in clips]
        return out

    def _hand_written_but_covered(self, stage: Stage, graph: dict) -> list[str]:
        """A custom stage whose working nodes a knowledge-base recipe already wires (checked, sized,
        with its notes) — the recipe was missed, not unavailable."""
        if not stage.custom:
            return []
        mine = {n.get("class_type") for n in graph.values() if isinstance(n, dict)} - _PLUMBING
        if not mine:
            return []
        hits = []
        for (fam, rid), recipe in self._catalog.recipes.items():
            theirs = {n.get("class_type") for n in recipe.graph.values() if isinstance(n, dict)} - _PLUMBING
            if theirs and len(mine & theirs) >= max(1, min(len(mine), len(theirs)) // 2 + (len(mine) > 1)):
                hits.append(f"{fam}/{rid} (task {recipe.task})")
        if not hits:
            return []
        return [f"stage {stage.name} is written by hand, but the knowledge base has recipes built from the same "
                f"nodes: {', '.join(sorted(hits)[:6])}. Use the recipe (kb_lookup family=<id> recipe=<id>) unless "
                "none of them does this job."]

    def _types(self, stage: Stage) -> set[str]:
        try:
            return set(self._builder.output_types(stage).values())
        except StageBuildError:
            return set()

    @staticmethod
    def _same_file_twice(stage: Stage, graph: dict) -> list[str]:
        """One file wired into two inputs of a stage — the person's own file or an earlier stage's
        output alike, and in a hand-written graph two loaders reading one slot."""
        by_source: dict[str, list[str]] = defaultdict(list)
        for i in stage.inputs:
            by_source["@" + i.role if not i.producer else i.source].append(i.name)
        for nid, node in (graph or {}).items():
            for field, value in ((node or {}).get("inputs") or {}).items():
                if isinstance(value, str) and value.startswith("@") and stage.custom:
                    by_source[value].append(f"node {nid}.{field}")
        return [
            f"stage {stage.name} reads {source} on {' and '.join(names)} — the same file twice adds nothing. "
            "Give the other input what the job needs and the file lacks (a still an earlier stage makes "
            "from it, e.g. full body in this job's outfit and setting, or the person's own photo), or a "
            "second file the person adds."
            for source, names in by_source.items() if len(names) > 1
        ]

    def _frames(self, stage: Stage) -> dict[str, str]:
        """{input: 'first' | 'last'} for inputs that BECOME a frame of the clip (knowledge base data)."""
        if stage.custom:
            return {}
        return {n: str(s["frame"]) for n, s in self._builder.recipe_of(stage).inputs.items() if s.get("frame")}

    def _prepared(self, stage: Stage) -> dict[str, str]:
        """{input: kind of signal} for inputs that take a PREPARED control signal (knowledge base data)."""
        if stage.custom:
            return {}
        return {n: str(s["prepared"]) for n, s in self._builder.recipe_of(stage).inputs.items() if s.get("prepared")}

    def _map_into_raw(self, pipeline: Pipeline, stage: Stage) -> list[str]:
        """The opposite mistake: a control MAP fed into an input that computes the map itself from the
        original footage — depth of a depth map, edges of an edge map."""
        if stage.custom:
            return []
        specs = self._builder.recipe_of(stage).inputs
        out = []
        for i in stage.inputs:
            kind = (specs.get(i.name) or {}).get("raw")
            producer = pipeline.stage(i.producer[0]) if i.producer else None
            if kind and producer is not None and producer.family == "control-maps":
                out.append(f"stage {stage.name}.{i.name} computes the {kind} itself from the ORIGINAL photo or clip, but "
                           f"it reads {producer.name}'s map — that is {kind} of a {kind} map. Bind the original "
                           "(user:<role>, or the stage that made the footage) and drop the map stage.")
        return out

    @staticmethod
    def _raw_into_prepared(stage: Stage, prepared: dict[str, str]) -> list[str]:
        """A person's raw photo or clip wired straight into an input that reads edges, depth or a pose
        skeleton: the model takes the footage itself for the control signal and renders garbage."""
        return [
            f"stage {stage.name} feeds @{i.role} straight into {i.name}, which takes a PREPARED {prepared[i.name]} "
            f"signal, not a raw photo or clip. Add a stage that makes it from @{i.role} (kb_lookup task control-map) "
            "and read its output here (stage:<name>.<output>) — unless the person's file already IS such a map."
            for i in stage.inputs if i.name in prepared and not i.producer
        ]

    @staticmethod
    def _video_from_raw_reference(stage: Stage, takes: dict[str, str], makes: dict[str, str],
                                  frames: dict[str, str]) -> list[str]:
        """A video whose every image input is a file the person adds: their look (and the outfit,
        setting and framing the video needs, which their photo rarely shows) is decided inside the
        slow, expensive step, and they see it for the first time in the finished video. Worse when
        the input IS a frame: the clip then opens (or ends) on their photo exactly as it was taken."""
        if _MOVING not in makes.values():
            return []
        stills = [i for i in stage.inputs if takes.get(i.name) == _STILL]
        if not stills or any(i.producer for i in stills):
            return []
        as_frame = [f"@{i.role} IS the {frames[i.name]} frame" for i in stills if i.name in frames]
        fact = (
            f"{'; '.join(as_frame)} — the clip {'opens' if any('first' in f for f in as_frame) else 'ends'} on "
            "that photo exactly as it was taken: its framing, clothes, place and light. That is right only "
            "when the job is to animate that very photo. "
            if as_frame else
            "Their look, outfit, setting and framing are decided inside the slow step, seen first in the "
            "finished video. "
        )
        return [
            f"stage {stage.name} makes a video straight from the person's own file(s) "
            f"({', '.join(sorted({'@' + i.role for i in stills}))}). " + fact +
            "When the brief asks for a different shot, place, outfit or framing than the file shows, an earlier "
            "image stage makes that still from the file (review: true) and this stage reads it "
            "(stage:<name>.<output>)."
        ]

    @staticmethod
    def _review_with_nothing_after(pipeline: Pipeline) -> list[str]:
        if len(pipeline.stages) < 2 or not pipeline.stages[-1].review:
            return []  # one stage: its result is the delivery, and the person sees it anyway
        last = pipeline.stages[-1].name
        return [
            f"stage {last} is a review point, but nothing runs after it — review belongs on the cheap "
            "still a slower stage reads, so a wrong look is caught before the slow step."
        ]


__all__ = ["PipelineDesignChecks"]

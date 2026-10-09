"""ApprovalCardBuilder — the approval card, built from the design that was CHECKED (pipeline_present).

The card is in ask_user's shape, so the window draws it like any ask and the run gate compares it
(PipelineCard): `title`, `workflows`, `questions`, `references`. Each step also carries its FACTS —
model, inputs, size, length, LoRAs, review — read off the stage's built graph (StageFactReader), and
the card carries what it `delivers`, what you get when it is `results` in several files, and what it
`imports`. The window shows those as short lines.

NO PROSE ABOUT THE DESIGN. The title is the design's name; it used to carry the agent's own
paragraph on why these models, and the step line its own note ("a 9-second 1280×704 video") beside a
design since changed to 10 s at 1024×1536. Numbers come from the graph, so the card says what runs.

Pure: the design, its graphs and the checked report in; the card out.
"""

from __future__ import annotations

from pipeline import Pipeline
from pipeline_validator import PipelineReport
from stage_builder import StageBuilder
from stage_fact_reader import StageFactReader

#: ask_user's own limit on steps and on questions (plugins/ask: _MAX_ROWS).
CARD_ROWS = 8


class ApprovalCardBuilder:
    def __init__(self, builder: StageBuilder, reader: StageFactReader) -> None:
        self._builder = builder
        self._reader = reader

    def build(self, pipeline: Pipeline, graphs: dict[str, dict], report: PipelineReport,
              cloud_files: set[str] | None) -> dict:
        facts = self._reader.facts(pipeline, graphs)
        workflows, questions, keys = [], [], []
        for stage in pipeline.stages:
            keys.append((stage.family, stage.recipe) if not stage.custom else ("custom", stage.name))
            f = facts[stage.name]
            workflows.append({"name": stage.name, "does": (stage.note or f["model"])[:300], "facts": f})
            prompt = f["prompt"]
            questions.append({"question": f"Prompt for {stage.name.replace('_', ' ')}", "default": prompt,
                              "_stage": stage.name} if len(prompt.split()) >= 8 else None)
        workflows, questions = _fit_card(workflows, questions, keys)
        if not questions:
            # A design with no prompt to read (an upscale, a restore) still needs one question: ask_user
            # refuses a card with nothing to answer, and the agent then rewrote the card to pass.
            questions.append({"question": "Anything to change before it is set up?",
                              "default": "Nothing — set it up as shown"})
        card = {
            "title": pipeline.name.replace("_", " "),
            "workflows": workflows,
            "questions": questions,
            "delivers": _delivers(pipeline, facts),
            "results": self._results(pipeline),
            "imports": _imports(report, cloud_files),
            # SEEDREAM PICTURES COST CREDITS: their expected price, before anything runs.
            "spend": _spend(facts),
        }
        references = _references(pipeline, report)
        if references:
            card["references"] = references
        return card

    def _results(self, pipeline: Pipeline) -> str:
        """SEVERAL results the person ends up with, said plainly — five separate clips where they
        asked for one video is then visible on the card, before anything runs."""
        read = {i.producer[0] for s in pipeline.stages for i in s.inputs if i.producer}
        ends = [s for s in pipeline.stages if s.name not in read]
        if len(ends) < 2:
            return ""
        kinds = []
        for s in ends:
            try:
                types = set(self._builder.output_types(s).values())
            except Exception:  # noqa: BLE001 — a stage the check already reported
                types = set()
            kinds.append("video" if "VIDEO" in types else "image" if "IMAGE" in types else "file")
        counts = {k: kinds.count(k) for k in dict.fromkeys(kinds)}
        return "You get " + " and ".join(f"{n} separate {k}{'s' if n > 1 else ''}" for k, n in counts.items())


def _spend(facts: dict[str, dict]) -> str:
    """What the design's Seedream pictures are expected to cost, in one line; '' when none."""
    images = [f for f in facts.values() if f.get("images")]
    if not images:
        return ""
    n = sum(int(f["images"]) for f in images)
    costs = [f.get("cost_usd") for f in images]
    price = f" ≈ ${sum(costs):.2f} in credits" if all(c is not None for c in costs) else ""
    return f"{n} Seedream 5 Pro picture{'s' if n > 1 else ''}{price}"


def _delivers(pipeline: Pipeline, facts: dict[str, dict]) -> str:
    """The size the card promises — only one the design sets. A last stage whose canvas follows an
    input image (a reference editor with no size port) makes whatever that photo's shape is: the card
    said "Delivers 1080x1350" and the result was 880x1184."""
    if not pipeline.deliver_size or not pipeline.stages:
        return ""
    last = pipeline.stages[-1]
    if facts[last.name]["size"]:
        return facts[last.name]["size"]
    follows = next((i.role.replace("_", " ") for i in last.inputs if not i.producer), "")
    return (f"aims for {pipeline.deliver_size} — the size follows "
            + (f"your {follows} image" if follows else "its input image"))


def _imports(report: PipelineReport, cloud: set[str] | None) -> str:
    """What setup brings in. Comfy Cloud already has most models: only what it lacks is imported —
    "Downloads 70 GB" for files it has was simply untrue. A file whose size nothing records is said
    as such ('0 GB' for a 40 GB model would be wrong). Without Comfy Cloud's list, every file counts."""
    def base(name: str) -> str:
        return name.replace("\\", "/").rsplit("/", 1)[-1]

    missing = {n: b for n, b in report.files.items() if cloud is None or base(n) not in cloud}
    if not missing:
        return "Nothing to import — every model is on Comfy Cloud"
    gb = sum(b or 0 for b in missing.values()) / 1e9
    unknown = sum(1 for b in missing.values() if b is None)
    size = (f"{gb:.0f} GB" if not unknown else
            f"at least {gb:.0f} GB, {unknown} size(s) unknown" if gb >= 1 else "size unknown")
    what = "Imports" if cloud is not None else "Downloads"
    names = ", ".join(base(n) for n in list(missing)[:3]) + (", …" if len(missing) > 3 else "")
    return f"{what} {len(missing)} model{'s' if len(missing) > 1 else ''} ({size}): {names}"


def _references(pipeline: Pipeline, report: PipelineReport) -> list[dict]:
    """The files the person adds, each with the steps that read it."""
    used_by: dict[str, list[str]] = {}
    for stage in pipeline.stages:
        for i in stage.inputs:
            if not i.producer and stage.name not in used_by.setdefault(i.role, []):
                used_by[i.role].append(stage.name)
    return [{"role": role, "what": "for " + ", ".join(used_by.get(role) or [what])}
            for role, what in sorted(report.user_inputs.items())]


def _fit_card(workflows: list[dict], questions: list, keys: list[tuple]) -> tuple[list[dict], list[dict]]:
    """ask_user shows at most CARD_ROWS steps and CARD_ROWS questions. A longer design (five angles,
    five clips, a join) is shown with consecutive steps that run the same recipe as ONE row, and their
    prompts as one question, a line per step — every step and every prompt still on it."""
    steps = [q for q in questions if q]
    if len(workflows) <= CARD_ROWS and len(steps) <= CARD_ROWS:
        return workflows, [{k: v for k, v in q.items() if k != "_stage"} for q in steps]
    rows, asks, i = [], [], 0
    while i < len(workflows):
        j = i
        while j + 1 < len(workflows) and keys[j + 1] == keys[i]:
            j += 1
        group = workflows[i:j + 1]
        rows.append(group[0] if len(group) == 1 else {
            "name": f"{group[0]['name']} … {group[-1]['name']}",
            "does": (f"{len(group)} steps: " + "; ".join(w["does"] for w in group))[:300],
            "facts": group[0]["facts"]})
        prompts = [q for q in questions[i:j + 1] if q]
        if len(prompts) == 1:
            asks.append({k: v for k, v in prompts[0].items() if k != "_stage"})
        elif prompts:
            asks.append({"question": "Prompts for " + ", ".join(q["_stage"].replace("_", " ") for q in prompts)
                         + " (one line each)",
                         "default": "\n".join(f"{q['_stage']}: {q['default']}" for q in prompts)})
        i = j + 1
    return rows, asks


__all__ = ["ApprovalCardBuilder", "CARD_ROWS"]

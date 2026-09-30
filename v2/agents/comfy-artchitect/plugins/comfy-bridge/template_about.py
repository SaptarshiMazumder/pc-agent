"""A template's ABOUT — `about.json` beside `template.json`: what the template does, told in full.

WHY. A template carried one optional line ("What it makes") and a word per input. The MiniMax
template never said it takes up to nine pictures, what `<Picture N>` means, or that its example
is a boy superhero and a mech dragon — so a render from two unrelated photos looked broken when it
had done exactly what its prompt said. The about is what a person reads before using a template,
and what the agent answers "can it do three characters?" from, instead of guessing.

    {"format": "comfy-penguin-about", "version": 1,
     "makes": "…",                          one or two lines
     "how_it_works": "…",                   a short paragraph
     "inputs": [{"role", "what", "tips"}],  what each input fixes, and how to pick a good one
     "you_can_change": ["…"],               what can be changed and how far
     "example": {"prompt": "…", "result": "…"} | null,
     "needs": {"runs_on": "…", "credits": "…", "vram": "…", "time": "…"},
     "limits": ["…"]}

The model and its checks only — reading the file, nothing else.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

FORMAT = "comfy-penguin-about"
VERSION = 1
FILE = "about.json"


@dataclass
class AboutInput:
    role: str
    what: str
    tips: str = ""


@dataclass
class TemplateAbout:
    makes: str
    how_it_works: str
    inputs: list[AboutInput] = field(default_factory=list)
    you_can_change: list[str] = field(default_factory=list)
    example: dict | None = None
    needs: dict = field(default_factory=dict)
    limits: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, folder: Path) -> tuple["TemplateAbout | None", str]:
        path = Path(folder) / FILE
        if not path.is_file():
            return None, f"it has no {FILE}"
        try:
            return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as e:
            return None, f"its {FILE} could not be read: {e}"

    @classmethod
    def from_dict(cls, data) -> tuple["TemplateAbout | None", str]:
        if not isinstance(data, dict) or data.get("format") != FORMAT:
            return None, f"{FILE} is not a {FORMAT}"
        if int(data.get("version") or 0) > VERSION:
            return None, f"{FILE} was made by a newer app (version {data.get('version')})"
        makes, how = _text(data.get("makes")), _text(data.get("how_it_works"))
        if not makes or not how:
            return None, f"{FILE} needs both `makes` and `how_it_works`"
        inputs = [AboutInput(role=_text(i.get("role")).lstrip("@"), what=_text(i.get("what")), tips=_text(i.get("tips")))
                  for i in data.get("inputs") or [] if isinstance(i, dict) and _text(i.get("role"))]
        example = data.get("example")
        example = ({"prompt": _text(example.get("prompt")), "result": _text(example.get("result"))}
                   if isinstance(example, dict) and _text(example.get("prompt")) else None)
        needs = {k: _text(v) for k, v in (data.get("needs") or {}).items()
                 if k in ("runs_on", "credits", "vram", "time") and _text(v)}
        return cls(makes=makes, how_it_works=how, inputs=inputs,
                   you_can_change=_lines(data.get("you_can_change")), example=example,
                   needs=needs, limits=_lines(data.get("limits"))), ""

    def to_dict(self) -> dict:
        return {"format": FORMAT, "version": VERSION, "makes": self.makes, "how_it_works": self.how_it_works,
                "inputs": [asdict(i) for i in self.inputs], "you_can_change": self.you_can_change,
                "example": self.example, "needs": self.needs, "limits": self.limits}

    def text(self) -> str:
        """The about as the agent reads it — to answer questions, never to recite."""
        lines = [f"What it makes: {self.makes}", f"How it works: {self.how_it_works}"]
        lines += [f"Input @{i.role}: {i.what}" + (f" Tips: {i.tips}" if i.tips else "") for i in self.inputs]
        lines += [f"You can change: {c}" for c in self.you_can_change]
        if self.example:
            lines.append(f"Its example: {self.example['prompt']}"
                         + (f" -> {self.example['result']}" if self.example.get("result") else ""))
        lines += [f"Needs ({k}): {v}" for k, v in self.needs.items()]
        lines += [f"Limit: {x}" for x in self.limits]
        return "\n".join(lines)


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _lines(value) -> list[str]:
    return [x.strip() for x in value if isinstance(x, str) and x.strip()] if isinstance(value, list) else []

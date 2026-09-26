"""A Library TEMPLATE, read: a whole chat's setup kept as one thing.

THE CONTRACT IS `template.json`, written by the window (app/src/agentd/library-template.ts) when
the person presses "Save as template to reuse", or unpacked from an uploaded `.template.zip`, or
shipped by the app as a suggested template. All three are the same folder:

    <template>/template.json
    <template>/workflows/<role>.api.json, <role>.json, install_<role>.py, install_<role>.manifest.json
    <template>/thumb.<ext>          optional

`template.json`: {format, version, name, description, inputs:[{role, what}],
steps:[{role, api, ui?, installer[], slots[]}]} with step paths relative to the folder.
This module only READS it — the Library's one writer is the window — and refuses a manifest it
does not understand in words, rather than half-loading it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

FORMAT = "comfy-penguin-template"
VERSION = 1
MANIFEST = "template.json"


@dataclass
class TemplateStep:
    role: str
    api: str
    ui: str = ""
    installer: list[str] = field(default_factory=list)
    slots: list[str] = field(default_factory=list)


@dataclass
class TemplateInput:
    role: str
    what: str = ""


class LibraryTemplate:
    """One template folder and its parsed manifest."""

    def __init__(self, folder: Path, name: str, description: str, inputs: list[TemplateInput],
                 steps: list[TemplateStep]) -> None:
        self.folder = Path(folder)
        self.name = name
        self.description = description
        self.inputs = inputs
        self.steps = steps

    @classmethod
    def load(cls, folder: Path) -> tuple["LibraryTemplate | None", str]:
        """The template in `folder`, or None and why not."""
        p = Path(folder) / MANIFEST
        if not p.is_file():
            return None, f"{Path(folder).name} has no {MANIFEST} — it is not a template"
        try:
            m = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            return None, f"{MANIFEST} could not be read: {e}"
        if not isinstance(m, dict) or m.get("format") != FORMAT:
            return None, f"{MANIFEST} is not a {FORMAT}"
        if int(m.get("version") or 0) > VERSION:
            return None, f"this template was made by a newer app (version {m.get('version')})"
        steps: list[TemplateStep] = []
        for s in m.get("steps") or []:
            if not isinstance(s, dict):
                continue
            api = str(s.get("api") or "")
            if not api.endswith(".api.json") or not _inside(api):
                return None, f"a step names no usable run file ({api or 'none'})"
            steps.append(TemplateStep(
                role=str(s.get("role") or Path(api).name[: -len(".api.json")]),
                api=api,
                ui=str(s.get("ui") or "") if _inside(str(s.get("ui") or "")) else "",
                installer=[str(x) for x in (s.get("installer") or []) if _inside(str(x))],
                slots=[str(x) for x in (s.get("slots") or [])],
            ))
        if not steps:
            return None, "the template has no workflows"
        inputs = [
            TemplateInput(role=str(i.get("role") or "").lstrip("@").strip(), what=str(i.get("what") or ""))
            for i in (m.get("inputs") or []) if isinstance(i, dict) and str(i.get("role") or "").strip()
        ]
        return cls(Path(folder), str(m.get("name") or Path(folder).name), str(m.get("description") or ""),
                   inputs, steps), ""

    def file(self, rel: str) -> Path:
        """A path the manifest names, inside this template's folder."""
        return self.folder / rel

    def missing(self) -> list[str]:
        """Files the manifest names that are not on disk."""
        named = [x for s in self.steps for x in (s.api, s.ui, *s.installer) if x]
        return [rel for rel in named if not self.file(rel).is_file()]

    def input_hints(self) -> dict[str, str]:
        """Each input role and what it is for."""
        return {i.role: i.what for i in self.inputs}


def _inside(rel: str) -> bool:
    """A manifest path that stays inside the template folder."""
    return bool(rel) and not rel.startswith("/") and "\\" not in rel and ".." not in rel.split("/")

"""TemplateGuide — a version-2 template's runbook: `guide.json` beside `template.json`.

ONE FILE THE AGENT RUNS FROM. Version 1 carried a setup guide (node packs and models) and an about
(for people), and left the agent to work out how to use the workflow — which is where it invented:
renamed nodes, rewrote prompts it was not asked to, a storyboard's three copies of its segments
edited one at a time. A version-2 template says, once, everything a run needs:

    {"format": "comfy-penguin-guide", "version": 1,
     "makes": "…",                      one or two lines, what the person gets
     "how_it_works": "…",              a short paragraph to answer questions from
     "models": [{"filename", "kind", "url"}],   each file the workflow names, and its link
     "settings": [{"name", "step", "kind", "node", "input"?, "what", "rules"?}],
     "run": "…",                        what to do: how to brief, what to ask, when to run
     "limits": ["…"]}

SETTINGS ARE THE ONLY THINGS A RUN CHANGES (template_set). `kind`:
    input         one node input, set to a value of the same type (a prompt, a seed, a size)
    ltx_director  an LTXDirector's storyboard: its segment prompts and lengths, kept in step
                  across the three places the node stores them (LtxDirectorStory)

Comfy Cloud runs only its preinstalled node packs, so a guide names no packs: a template whose
nodes Comfy Cloud lacks cannot be one. The model and its checks only — reading the file, nothing else.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

FORMAT = "comfy-penguin-guide"
VERSION = 1
FILE = "guide.json"
SETTING_KINDS = ("input", "ltx_director")


@dataclass
class GuideModel:
    filename: str
    kind: str
    url: str


@dataclass
class GuideSetting:
    name: str
    step: str
    kind: str
    node: str
    input: str = ""
    what: str = ""
    rules: str = ""


@dataclass
class TemplateGuide:
    makes: str
    how_it_works: str
    models: list[GuideModel]
    settings: list[GuideSetting]
    run: str
    limits: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, folder: Path) -> tuple["TemplateGuide | None", str]:
        """The guide in `folder`, or None and why not."""
        p = Path(folder) / FILE
        if not p.is_file():
            return None, f"no {FILE}"
        try:
            g = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            return None, f"{FILE} could not be read: {e}"
        if not isinstance(g, dict) or g.get("format") != FORMAT:
            return None, f"{FILE} is not a {FORMAT}"
        if int(g.get("version") or 0) > VERSION:
            return None, f"{FILE} was made by a newer app (version {g.get('version')})"
        models = [GuideModel(str(m.get("filename") or ""), str(m.get("kind") or ""), str(m.get("url") or ""))
                  for m in g.get("models") or [] if isinstance(m, dict)]
        bad = [m.filename or "?" for m in models if not (m.filename and m.kind and m.url.startswith("https://"))]
        if bad:
            return None, f"{FILE}: a model needs a filename, its folder and an https link — {', '.join(bad)}"
        settings = []
        for s in g.get("settings") or []:
            if not isinstance(s, dict):
                continue
            setting = GuideSetting(name=str(s.get("name") or ""), step=str(s.get("step") or ""),
                                   kind=str(s.get("kind") or ""), node=str(s.get("node") or ""),
                                   input=str(s.get("input") or ""), what=str(s.get("what") or ""),
                                   rules=str(s.get("rules") or ""))
            if not (setting.name and setting.step and setting.node and setting.kind in SETTING_KINDS):
                return None, f"{FILE}: setting '{setting.name or '?'}' needs a name, a step, a node and a kind ({', '.join(SETTING_KINDS)})"
            if setting.kind == "input" and not setting.input:
                return None, f"{FILE}: setting '{setting.name}' sets one input — name it"
            settings.append(setting)
        if not str(g.get("run") or "").strip():
            return None, f"{FILE} says nothing about how to run it ('run')"
        return cls(makes=str(g.get("makes") or ""), how_it_works=str(g.get("how_it_works") or ""),
                   models=models, settings=settings, run=str(g["run"]).strip(),
                   limits=[str(x) for x in g.get("limits") or []]), ""

    def setting(self, name: str) -> GuideSetting | None:
        return next((s for s in self.settings if s.name == name), None)

    def model_for(self, name: str) -> GuideModel | None:
        """The guide's entry for a file the workflow names (matched on its last path part)."""
        base = name.replace("\\", "/").rsplit("/", 1)[-1].lower()
        return next((m for m in self.models if m.filename.replace("\\", "/").rsplit("/", 1)[-1].lower() == base), None)

    def settings_text(self) -> str:
        """The settings, one line each, for the agent."""
        return "\n".join(f"  {s.name} ({s.kind}, step {s.step}) — {s.what}" + (f" RULES: {s.rules}" if s.rules else "")
                         for s in self.settings) or "  none — it runs exactly as it is"


__all__ = ["FILE", "GuideModel", "GuideSetting", "SETTING_KINDS", "TemplateGuide"]

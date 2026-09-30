"""template_about_draft — the WINDOW's call when a chat is saved as a template: a drafted about.

The facts come from the chat (TemplateAboutFactsBuilder); a language model turns them into the
about's sections, in plain words, from those facts only. The Save dialog shows the draft for the
person to edit, and the window writes `about.json` (the Library's one writer is the window). The
agent never calls this.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_workspace
from agent_runtime.application.tool_models import brain_model

from template_about import FORMAT, VERSION, AboutInput, TemplateAbout
from template_about_facts_builder import TemplateAboutFactsBuilder

_INSTRUCTIONS = """You write the "About this template" page of a ComfyUI template, for a person who
has never seen it. Use ONLY the facts below; never invent a capability, a number or a model. Plain,
short sentences; no marketing words. Answer with ONE JSON object and nothing else:

{"makes": "one or two sentences: what goes in and what comes out",
 "how_it_works": "a short paragraph: how the inputs and the prompt become the result, including how
   the prompt refers to the inputs (tags like <Picture 1>, in the order they are connected) when
   the notes say so",
 "inputs": [{"role": "<slot role, without @>", "what": "what this input fixes in the result",
             "tips": "how to pick a good one"}],
 "you_can_change": ["what can be changed and how far — e.g. more reference inputs, length,
   resolution, a quality/speed setting — only where the notes or settings show it"],
 "example": {"prompt": "the shipped prompt, shortened to its gist", "result": "what it produces"}
            or null when no prompt is shipped,
 "needs": {"runs_on": "the machine's GPU, or which paid service", "credits": "0, or the paid cost",
           "vram": "only if the notes say", "time": "from the completed runs, if any"},
 "limits": ["what makes results worse or slower, from the notes and settings"]}

One entry in "inputs" per input the person fills, with exactly those roles.

FACTS:
"""


class TemplateAboutDraftTool(Tool):
    name = "template_about_draft"
    label = "Draft a template's about"
    needs_model = True
    model_kind = "text"
    default_timeout_sec = 120.0
    description = (
        "For the WINDOW's Save-as-template dialog: drafts the template's About page from the "
        "chat's workflows, notes, settings, inputs and runs. The model has no reason to call it."
    )
    parameters = {
        "type": "object",
        "required": ["name", "steps"],
        "properties": {
            "name": {"type": "string"},
            "description": {"type": "string"},
            "steps": {
                "type": "array",
                "items": {"type": "object"},
                "description": "[{role, api, ui?, manifests: [...]}], workspace-relative paths.",
            },
            "inputs": {"type": "array", "items": {"type": "object"}, "description": "[{role, what}]"},
        },
    }

    def __init__(self, config) -> None:
        self.config = config

    async def execute(self, tool_call_id, params, abort, on_update=None):
        inputs = [i for i in params.get("inputs") or [] if isinstance(i, dict) and i.get("role")]
        try:
            facts = TemplateAboutFactsBuilder(Path(current_workspace(".") or ".")).build(
                name=str(params.get("name") or ""), description=str(params.get("description") or ""),
                steps=[s for s in params.get("steps") or [] if isinstance(s, dict)], inputs=inputs,
            )
        except (OSError, ValueError) as e:
            return ToolResult.text(f"template_about_draft: {e}", is_error=True)
        model = self.resolve_model(self.config) or brain_model(self.config)
        try:
            raw = await asyncio.to_thread(
                self.models.text, model=model, prompt=_INSTRUCTIONS + facts, max_tokens=1800, timeout=90,
            )
        except Exception as e:  # noqa: BLE001 — a model or proxy failure is said, never hidden
            return ToolResult.text(f"template_about_draft: the model could not write it ({type(e).__name__}: {e})",
                                   is_error=True)
        match = re.search(r"\{.*\}", raw or "", re.S)
        try:
            data = json.loads(match.group(0)) if match else None
        except ValueError:
            data = None
        if not isinstance(data, dict):
            return ToolResult.text("template_about_draft: the model did not answer with the about's JSON", is_error=True)
        about, why = TemplateAbout.from_dict({**data, "format": FORMAT, "version": VERSION})
        if about is None:
            return ToolResult.text(f"template_about_draft: {why}", is_error=True)
        # Every input the person fills is described, with its role exactly — the model may drop one.
        described = {i.role for i in about.inputs}
        for i in inputs:
            role = str(i["role"]).lstrip("@")
            if role not in described:
                about.inputs.append(AboutInput(role=role, what=str(i.get("what") or "")))
        return ToolResult.text(about.makes, details={"about": about.to_dict(), "model": model})

"""template_use — bring a whole Library template into this chat.

WHAT IT DOES. Every workflow in the template (run file, ComfyUI file, installer) is copied into
this chat's workflows folder under its role, exactly as `library_use` brings one workflow in:
the design now exists here, and its reference slots are declared — with what each is for, from
the template — so the Inputs tab lists them before any file is added. Then it answers with what
the agent needs to brief the person: the template's name and description, its steps in run
order with what each graph is (nodes, slots, models), and the inputs to fill.

WHAT IT DOES NOT DO: change a workflow, run anything, or pick references. A template is reused
as it is unless the person says otherwise; changing one is the normal research → emit →
validate path, per workflow.

A TEMPLATE RUNS WITHOUT THE ASK. Every step is marked as the template's (studio_state
.mark_template_step), so comfy_run does not wait for an approval card: the person chose a
finished setup and is told what a run costs. Rewriting a step removes its mark — a changed step
is a new design, and it is asked about like one.

THE BRIEF IS WHAT IT MAKES, NOT HOW. The answer lists steps by role and the inputs, never the
nodes and models inside them: that is what the agent repeated to the person, unasked.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_workspace

import chat_paths
import library_paths
import reference_slots
import studio_state
from fixed_design_shape import FixedDesignShape
from library_index import LibraryIndex
from library_template import LibraryTemplate, TemplateInput
from template_guide import FILE as GUIDE_FILE, TemplateGuide
from workflow_summary import WorkflowSummary


class TemplateUseTool(Tool):
    name = "template_use"
    label = "Use a template"
    description = (
        "Bring a Library TEMPLATE (a whole saved setup: several workflows, their installers and "
        "the inputs they need) into this chat. Every workflow is copied in under its role and "
        "its reference slots are declared, so the Inputs tab shows what to add. Returns the "
        "template's runbook: what it makes, its steps, the inputs, the SETTINGS a run may change "
        "(template_set — nothing else), and how to run it. A template's steps run without ask_user "
        "or comfy_validate — follow the runbook. Only version-2 templates are usable."
    )
    parameters = {
        "type": "object",
        "properties": {
            "item": {"type": "string", "description": "The template's id (tpl_…) or name, from library_find."},
        },
        "required": ["item"],
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        ws = Path(current_workspace(".") or ".")
        idx = LibraryIndex.load(ws)
        if idx.problem:
            return ToolResult.text(f"template_use: {idx.problem}", is_error=True)
        item, why = idx.resolve(str(params.get("item") or ""))
        if item is None:
            return ToolResult.text(f"template_use: {why}", is_error=True)
        if item.kind != "template":
            return ToolResult.text(
                f"template_use: {item.name} is a {item.kind}, not a template — bring it in with library_use.",
                is_error=True,
            )
        template, problem = LibraryTemplate.load(library_paths.item_path(ws, item.path))
        if template is None:
            return ToolResult.text(f"template_use: {problem}", is_error=True)
        missing = template.missing()
        if missing:
            return ToolResult.text(
                f"template_use: {item.name} is incomplete — missing {', '.join(missing)}. "
                "It may still be uploading, or was saved from an older chat: design the job from the "
                "knowledge base instead (kb_lookup, pipeline_plan) and mention that the template was incomplete.",
                is_error=True,
            )
        try:
            return self._bring_in(ws, item.id, template)
        except (OSError, ValueError) as e:
            return ToolResult.text(f"template_use failed: {e}", is_error=True)

    def _bring_in(self, ws: Path, item_id: str, template: LibraryTemplate) -> ToolResult:
        folder = chat_paths.chat_rel(chat_paths.WORKFLOWS)
        dest = ws / folder
        dest.mkdir(parents=True, exist_ok=True)
        hints = template.input_hints()
        steps_text: list[str] = []
        brought: list[dict] = []
        all_slots: list[str] = []

        for n, step in enumerate(template.steps, 1):
            role = step.role
            api_src = template.file(step.api)
            graph = json.loads(api_src.read_text(encoding="utf-8"))
            if WorkflowSummary(graph).format != "api":
                raise ValueError(f"{step.api} is not an API-format graph")
            shutil.copyfile(api_src, dest / f"{role}.api.json")
            if step.ui:
                shutil.copyfile(template.file(step.ui), dest / f"{role}.json")
            for inst in step.installer:
                shutil.copyfile(template.file(inst), dest / Path(inst).name)
            roles = list(reference_slots.roles_in(graph))
            if roles:
                reference_slots.record(ws, role, roles, {r: hints.get(r, "") for r in roles})
                all_slots += [r for r in roles if r not in all_slots]
            try:
                studio_state.mark_emitted()
                studio_state.mark_first_emit(role)
            except Exception:  # noqa: BLE001 — bookkeeping must not fail the copy
                pass
            # Not swallowed: without the mark the step would stop for an ask it should not need.
            studio_state.mark_template_step(role)
            studio_state.mark_fixed_design()
            studio_state.mark_fixed_shape(role, FixedDesignShape.of(graph).to_json())
            steps_text.append(f"{n}. {role}  ({folder}/{role}.api.json)")
            brought.append({"role": role, "api": f"{folder}/{role}.api.json"})

        # THE GUIDE comes along: template_setup imports from it, template_set changes only what it
        # lists. A version-2 template without one is not a template that runs as it is.
        guide, no_guide = TemplateGuide.load(template.folder)
        if guide is None:
            raise ValueError(f"the template's guide is unusable: {no_guide}")
        (dest / GUIDE_FILE).unlink(missing_ok=True)
        (dest / "setup.json").unlink(missing_ok=True)  # a version-1 template's, from before
        shutil.copyfile(template.folder / GUIDE_FILE, dest / GUIDE_FILE)

        # A template saved before its chat declared inputs still has slots in its graphs.
        inputs = template.inputs or [TemplateInput(role=r) for r in all_slots]
        inputs_text = (
            "\n".join(f"  @{i.role}" + (f" — {i.what}" if i.what else "") for i in inputs)
            if inputs else "  none — it runs as it is"
        )
        limits = "".join(f"\n  - {x}" for x in guide.limits)
        return ToolResult.text(
            f"brought template '{template.name}' into this chat — {len(template.steps)} step(s), in run order:\n"
            + "\n".join(steps_text)
            + f"\n\nWHAT IT MAKES: {guide.makes or template.description}"
            + (f"\nHOW IT WORKS (answer questions from this; do not recite it): {guide.how_it_works}"
               if guide.how_it_works else "")
            + f"\n\nINPUTS the user fills on the Inputs tab (comfy_run refuses while any is empty):\n{inputs_text}"
            + "\n\nSETTINGS — the ONLY things a run changes, with template_set (no settings = see their values):\n"
            + guide.settings_text()
            + (f"\n\nLIMITS:{limits}" if limits else "")
            + f"\n\nHOW TO RUN IT (the template's own instructions — follow them):\n{guide.run}"
            + "\n\nTHE TOOLS: template_setup (imports the models Comfy Cloud lacks, from the guide), "
            "template_set (the settings above), comfy_price, comfy_run each step in order. No ask_user, no "
            "comfy_validate, and NEVER comfy_emit or stage tools on a template step — a template is changed "
            "only through its settings; a change beyond them is a new design, through the normal protocol.",
            details={"template": item_id, "workflows": brought, "inputs": [i.role for i in inputs]},
        )

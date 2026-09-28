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
from library_index import LibraryIndex
from library_template import LibraryTemplate, TemplateInput
from template_setup_guide import FILE as SETUP_FILE, TemplateSetupGuide
from workflow_summary import WorkflowSummary


class TemplateUseTool(Tool):
    name = "template_use"
    label = "Use a template"
    description = (
        "Bring a Library TEMPLATE (a whole saved setup: several workflows, their installers and "
        "the inputs they need) into this chat. Every workflow is copied in under its role and "
        "its reference slots are declared, so the Inputs tab shows what to add. Returns the "
        "template's description, its steps in run order and the inputs. A template's steps run "
        "without ask_user or comfy_validate — see the result for what to do next."
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
                "Tell the user; it may still be uploading, or was saved from an older chat.",
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
            steps_text.append(f"{n}. {role}  ({folder}/{role}.api.json)")
            brought.append({"role": role, "api": f"{folder}/{role}.api.json"})

        # THE SETUP GUIDE comes along, for template_setup to install from. A template without one
        # (saved before guides existed) is set up the normal way — the only time the agent works
        # its sources out itself.
        guide, no_guide = TemplateSetupGuide.load(template.folder)
        (dest / SETUP_FILE).unlink(missing_ok=True)
        if guide is not None:
            shutil.copyfile(template.folder / SETUP_FILE, dest / SETUP_FILE)
        setup = (
            "comfy_price each step (by its path) for the credits one run costs"
            if guide is not None else
            f"this template has NO setup guide ({no_guide}): comfy_price each step"
        )
        on_go = (
            "ON GO: template_setup (it installs every node pack and model from the template's "
            "setup guide, and names any gap), then comfy_run each step in order, as it is. Work "
            "out ONLY the gaps template_setup names — never a link, a model or a node it already set up."
            if guide is not None else
            "ON GO: this template has no setup guide, so for each step in order: comfy_validate, "
            "comfy_install / comfy_node_install exactly what it names (no ask), comfy_run."
        )

        # A template saved before its chat declared inputs still has slots in its graphs.
        inputs = template.inputs or [TemplateInput(role=r) for r in all_slots]
        inputs_text = (
            "\n".join(f"  @{i.role}" + (f" — {i.what}" if i.what else "") for i in inputs)
            if inputs else "  none — it runs as it is"
        )
        return ToolResult.text(
            f"brought template '{template.name}' into this chat — {len(template.steps)} workflow(s), in run order:\n"
            + "\n".join(steps_text)
            + (f"\n\nwhat it makes: {template.description}" if template.description else "")
            + f"\n\ninputs the user fills on the Inputs tab (comfy_run refuses while any is empty):\n{inputs_text}"
            + f"\n\nNOW: {setup}. Then tell the "
            "user in a few short lines: what the template makes, the inputs to add on the Inputs tab, "
            "and the credits a run costs — and that they add the inputs and say go, or say what to "
            "change. No models, nodes or settings unless they ask. End the turn.\n"
            + on_go + " No ask_user. NEVER comfy_emit a template step to get it running — a step is "
            "rewritten only when the user asks for a change, and then through the normal protocol, ask included.",
            details={"template": item_id, "workflows": brought, "inputs": [i.role for i in inputs]},
        )

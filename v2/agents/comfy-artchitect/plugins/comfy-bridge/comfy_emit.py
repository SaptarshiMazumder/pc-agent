"""comfy_emit — a served design (a template step or a Library workflow) re-written with new values.

New designs are pipelines (pipeline_plan); this tool refuses one. For a served design it normalises
the links, holds the design to its own wiring, refuses a loader that reads a filename the model
typed, checks the reference slots — and hands the graph to WorkflowFileWriter, which writes
both of ComfyUI's shapes (`<name>.api.json` to run, `<name>.json` to import; see that module for
why both, and why this never converts one into the other).
"""

from __future__ import annotations

from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_workspace

import reference_slots
import studio_state
from fixed_design_shape import FixedDesignShape
from workflow_link import WorkflowLink
from workflow_file_writer import WorkflowFileWriter


def _slug(name: str) -> str:
    keep = [c if c.isalnum() or c in "-_" else "-" for c in (name or "").strip().lower()]
    return "".join(keep).strip("-") or "workflow"


class ComfyEmitTool(Tool):
    name = "comfy_emit"
    label = "Write a ComfyUI workflow"
    default_retryable = False
    description = (
        "Only for a workflow that came from a TEMPLATE or the LIBRARY: re-write it with new input "
        "values (prompt, image, size, length), every node and link as it was, in BOTH ComfyUI "
        "formats. A new design is never written here — that is pipeline_plan. The NAME is the "
        "workflow's own name in this chat."
    )
    parameters = {
        "type": "object",
        "required": ["name", "nodes"],
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "The workflow's ROLE — 'storyboard', 'video', 'upscale'. Reuse it for every "
                    "revision; the file is replaced, not duplicated."
                ),
            },
            "nodes": {
                "type": "array",
                "description": (
                    "The graph, in order. Each entry: {id, class_type, inputs}. An input value "
                    "is either a literal, or a link written as [upstream_id, output_slot] — the "
                    "same shape ComfyUI's API format uses. Use a text upstream_id and a "
                    'non-negative integer output_slot, e.g. ["9", 0].'
                ),
                "items": {
                    "type": "object",
                    "required": ["id", "class_type", "inputs"],
                    "properties": {
                        "id": {"type": "string"},
                        "class_type": {"type": "string"},
                        "inputs": {"type": "object"},
                        "title": {"type": "string"},
                    },
                },
            },
            "references": {
                "type": "array",
                "description": (
                    "What each reference SLOT is for. A loader whose file input is the token "
                    "`@model` declares the slot 'model'; the user fills it in the References "
                    "panel with a file named by that role, and comfy_run uploads and wires it. "
                    "One entry per token in the graph: {role, what}."
                ),
                "items": {
                    "type": "object",
                    "required": ["role", "what"],
                    "properties": {
                        "role": {"type": "string", "description": "e.g. 'model', 'garment', 'start_frame'."},
                        "what": {"type": "string", "description": "What the file must show, e.g. 'the influencer, face visible'."},
                    },
                },
            },
            "note": {
                "type": "string",
                "description": "One line on what this workflow does; kept beside the files.",
            },
            "user_asked": {
                "type": "string",
                "description": (
                    "Only when changing the nodes or wiring of a workflow that came from a "
                    "TEMPLATE or the LIBRARY, or adding a workflow beside one: the user's own "
                    "words asking for this change. Setting its inputs needs no user_asked; a "
                    "served design is never rewired to get it running."
                ),
            },
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            name = _slug(str(params.get("name") or ""))
            user_asked = str(params.get("user_asked") or "").strip()
            nodes = params.get("nodes")
            if not isinstance(nodes, list) or not nodes:
                return ToolResult.text("nodes must be a non-empty array", is_error=True)

            api: dict = {}
            ids = set()
            for node in nodes:
                if not isinstance(node, dict):
                    return ToolResult.text(f"not a node object: {node!r}", is_error=True)
                nid = str(node.get("id") or "").strip()
                cls = str(node.get("class_type") or "").strip()
                inputs = node.get("inputs")
                if not nid or not cls or not isinstance(inputs, dict):
                    return ToolResult.text(
                        f"node {nid or '?'} needs id, class_type and an inputs object",
                        is_error=True,
                    )
                if nid in ids:
                    return ToolResult.text(f"two nodes share id {nid}", is_error=True)
                ids.add(nid)
                entry = {"class_type": cls, "inputs": dict(inputs)}
                if node.get("title"):
                    entry["_meta"] = {"title": str(node["title"])}
                api[nid] = entry

            # Normalize links in the graph that BOTH serializers consume, not just in the
            # existence check: ComfyUI looks up prompt[upstream_id] without coercing it.
            for nid, entry in api.items():
                for field, value in entry["inputs"].items():
                    try:
                        link = WorkflowLink.from_input(value, api, normalize_node_id=True)
                    except ValueError as exc:
                        return ToolResult.text(
                            f"node {nid}.{field} {exc}", is_error=True,
                        )
                    if link is not None:
                        entry["inputs"][field] = link.as_input()

            # A SERVED DESIGN KEEPS ITS NODES AND WIRING (fixed_design_shape). A template step or
            # a Library workflow is re-emitted only to set its inputs; the agent once rewrote one
            # from memory, dropped its sampler and rendered grey. A new workflow beside it is the
            # same rewrite under another name. Only the person's own request changes either.
            shapes = studio_state.fixed_shapes()
            if not shapes:
                # A NEW DESIGN IS A PIPELINE. Designs are built from the knowledge base and checked
                # stage by stage (pipeline_plan); a graph written here from memory skips both.
                return ToolResult.text(
                    "comfy_emit only re-writes a workflow brought from a template or the Library, and "
                    "this chat has none. Design with kb_lookup, then pipeline_plan — a model no recipe "
                    "covers is a custom stage there (`nodes` + `outputs`).",
                    is_error=True,
                )
            if not user_asked:
                if name not in shapes:
                    return ToolResult.text(
                        f"this chat runs a design it was given ({', '.join(sorted(shapes))}), and it "
                        f"runs as it is — no new workflow '{name}' is written beside it. Set its "
                        "inputs by re-emitting it under its own name. If the USER asked for a new "
                        "workflow, call again with user_asked set to their words.",
                        is_error=True,
                    )
                diffs = FixedDesignShape.from_json(shapes[name]).differences(api)
                if diffs:
                    return ToolResult.text(
                        f"'{name}' came from a template or the Library: its nodes and wiring stay as "
                        "they are; only input values change. This emit changed its structure:\n  "
                        + "\n  ".join(diffs[:20])
                        + (f"\n  … and {len(diffs) - 20} more" if len(diffs) > 20 else "")
                        + f"\nRe-emit every node of {name}.api.json with the same id, class and "
                        "links, changing only values. A value the machine does not accept is "
                        "fixed as a value; a missing node class is a pack to install. If the "
                        "design itself is broken, tell the user what and ask — their words go in "
                        "user_asked.",
                        is_error=True,
                    )

            # A LOADER READS A SLOT OR AN UPLOAD, NEVER A NAME THE MODEL TYPED. `@role` is the
            # slot; a literal is allowed only if comfy_upload returned it this conversation or
            # comfy_download brought it back. Anything else — the instance's example.png, a
            # remembered filename, a guess — is the graph quietly reading a file the user never
            # gave it, which is the failure rule 14 exists to stop. Checked here, one round trip.
            known = set(studio_state.uploaded_in_session()) | {
                Path(rel).name for rel in studio_state.downloaded_in_session()
            }
            for nid, entry in api.items():
                for field, value in entry["inputs"].items():
                    if field not in reference_slots._SLOT_FIELDS or not isinstance(value, str):
                        continue
                    if reference_slots.role_of(value) is not None or value in known or Path(value).name in known:
                        continue
                    return ToolResult.text(
                        f"node {nid}.{field} = {value!r}: a loader reads a SLOT (`@role`, which the "
                        "user fills in the References panel and comfy_run wires in) or a name "
                        "comfy_upload returned in this conversation — not a filename you typed.",
                        is_error=True,
                    )

            # REFERENCE SLOTS: every `@role` on a loader input is a slot the user fills by file
            # (reference_slots). Validated here so a typo is one round trip, not a refused run.
            slot_problems = reference_slots.bad_roles(api)
            if slot_problems:
                return ToolResult.text("bad reference slot(s):\n  " + "\n  ".join(slot_problems), is_error=True)
            roles = list(reference_slots.roles_in(api))
            whats = {
                str(r.get("role") or "").strip().lstrip(reference_slots.TOKEN): str(r.get("what") or "").strip()
                for r in (params.get("references") or [])
                if isinstance(r, dict)
            }
            undeclared = [r for r in whats if r not in roles]
            if undeclared:
                return ToolResult.text(
                    "references describe role(s) no node uses: " + ", ".join(undeclared)
                    + ". Put the token on the loader's file input (e.g. LoadImage.image = "
                    f"'{reference_slots.TOKEN}{undeclared[0]}') or drop the entry.",
                    is_error=True,
                )

            # The files, the slot record and the "a design exists" marks — one writer, shared
            # with the pipeline tools, so a stage and an emitted workflow are the same thing on disk.
            written = WorkflowFileWriter(Path(current_workspace(".") or ".")).write(
                name, api, whats, order=[str(n.get("id")) for n in nodes], forget_fixed_shape=bool(user_asked),
            )
            api_rel, ui_rel, digest, installer_note = (
                written.api_rel, written.ui_rel, written.digest, written.installer_note)
            note = str(params.get("note") or "").strip()
            slots = ""
            if roles:
                slots = (
                    "\nreference slots — the user fills them in the References panel; comfy_run "
                    "uploads and wires them, and REFUSES while any is EMPTY (do not ask for "
                    "uploads in prose, do not wait — validate now, run when they are filled):\n"
                    + written.slots_text
                )
            return ToolResult.text(
                f"wrote {len(api)} nodes (graph {digest})"
                + (f" — {note}" if note else "")
                + f"\n  run this:    {api_rel}"
                + f"\n  import this: {ui_rel}"
                + slots
                + installer_note
                + "\nRun it with comfy_run before calling it finished — a workflow that was "
                "written has not yet been shown to work.",
                details={"api": api_rel, "ui": ui_rel, "nodes": len(api), "slots": roles},
                artifacts=[api_rel, ui_rel],
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"comfy_emit failed: {type(e).__name__}: {e}", is_error=True)

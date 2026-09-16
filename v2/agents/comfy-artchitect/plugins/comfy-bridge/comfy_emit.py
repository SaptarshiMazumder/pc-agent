"""comfy_emit — one design, written out in both of ComfyUI's JSON shapes.

WHY BOTH. `POST /prompt` accepts ONLY the API shape; the browser imports only the UI shape. An
agent that emits one of them leaves the user either unable to run it or unable to open it. So
the agent designs once, in the simple node list below, and this writes:

    <name>.api.json   what comfy_run submits
    <name>.json       what the user drags into their ComfyUI

GENERATING BOTH IS TRACTABLE; CONVERTING BETWEEN THEM IS NOT. Going UI -> API means replaying
litegraph semantics — positional `widgets_values`, the extra element `control_after_generate`
injects, muted and bypassed nodes that must be elided with their links rewired. Going the other
way from a design we authored is arithmetic, because we already know every field's name. That
asymmetry is why this tool emits and never converts, and why comfy_run refuses a UI file with a
pointer to `Export (API)` instead of trying.

THE UI FILE IS A CONVENIENCE, AND SAYS SO. Its layout is a plain left-to-right grid rather than
anything considered, and widget order is taken from the instance's own `input_order` where the
caller supplied it. If the two disagree, the API file is the one that ran.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_workspace

import chat_paths
import reference_slots
from workflow_link import WorkflowLink
from workflow_installer_exporter import WorkflowInstallerExporter


def _slug(name: str) -> str:
    keep = [c if c.isalnum() or c in "-_" else "-" for c in (name or "").strip().lower()]
    return "".join(keep).strip("-") or "workflow"


class ComfyEmitTool(Tool):
    name = "comfy_emit"
    label = "Write a ComfyUI workflow"
    default_retryable = False
    description = (
        "Write a designed workflow to disk in BOTH ComfyUI formats: the API file comfy_run "
        "submits, and the UI file the user imports into their browser. Give it the node list; "
        "it handles ids, links and layout. Use names that comfy_inventory confirmed exist. "
        "The NAME is the workflow's ROLE (storyboard, video, upscale) and stays the same for "
        "the whole conversation: a revision is emitted under the same name and replaces the "
        "file — never a new name for a new draft."
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
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            name = _slug(str(params.get("name") or ""))
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

            # A LOADER READS A SLOT OR AN UPLOAD, NEVER A NAME THE MODEL TYPED. `@role` is the
            # slot; a literal is allowed only if comfy_upload returned it this conversation or
            # comfy_download brought it back. Anything else — the instance's example.png, a
            # remembered filename, a guess — is the graph quietly reading a file the user never
            # gave it, which is the failure rule 14 exists to stop. Checked here, one round trip.
            import studio_state

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

            ui = self._ui_graph(nodes, api)

            # Inside the run's workspace — never computed from __file__, which is where the
            # plugin lives rather than where this user's files go — and in THIS chat's folder.
            root = Path(current_workspace(".") or ".") / chat_paths.chat_rel(chat_paths.WORKFLOWS)
            root.mkdir(parents=True, exist_ok=True)
            api_path = root / f"{name}.api.json"
            ui_path = root / f"{name}.json"
            installer_note = ""
            try:
                WorkflowInstallerExporter.invalidate(api_path)
            except OSError:
                installer_note = "\nOld installer invalidation failed; download a fresh installer after validation."
            api_path.write_text(json.dumps(api, indent=2) + "\n", encoding="utf-8")
            ui_path.write_text(json.dumps(ui, indent=2) + "\n", encoding="utf-8")

            # WORKSPACE-RELATIVE NAMES, not the absolute ones just written to.
            #
            # `current_workspace()` answers in the SANDBOX's coordinates, and on a microVM the
            # executor mounts the tree at its own prefix — /tmp/exec-<id>/ws. The files land
            # correctly and sync back to the real workspace, but every path this tool NAMES is
            # then a guest path: the window cannot resolve it, so the file rail reads "Nothing
            # written yet" directly beside a message that just listed two workflows, and
            # `read`/`show_files` refuse the names as outside the run's visible filesystem.
            #
            # Relative means the same file to the sandbox, the fs tools and the window alike — the
            # only form that survives the guest/host boundary. Subprocess sandboxes never showed
            # this, because there both sides are one filesystem.
            #
            # AND INSIDE THIS CHAT'S OWN FOLDER (chat_paths): the workspace is the account's, so a
            # flat workflows/ held every conversation's files at once, and two jobs that named a
            # workflow the same overwrote each other's. The window lists exactly this folder.
            folder = chat_paths.chat_rel(chat_paths.WORKFLOWS)
            api_rel = f"{folder}/{api_path.name}"
            ui_rel = f"{folder}/{ui_path.name}"

            # THE DESIGN NOW EXISTS, which is what unlocks comfy_inventory — see studio_state.
            try:
                import studio_state

                studio_state.mark_emitted()
                # And WHEN this name first existed here — what decides whether a checkpoint
                # presented later covers it (studio_state.checkpoint_answered).
                studio_state.mark_first_emit(name)
            except Exception:  # noqa: BLE001 — a telemetry miss must not fail the emit
                pass

            note = str(params.get("note") or "").strip()
            # THE GRAPH'S FINGERPRINT IS PART OF THE ANSWER. A re-emit that changed the graph
            # must read as a new result, or the liveness detector counts "wrote 9 nodes" three
            # times over as a model going nowhere while it was actually converging.
            digest = hashlib.sha1(
                json.dumps(api, sort_keys=True).encode("utf-8")
            ).hexdigest()[:8]
            ws = Path(current_workspace(".") or ".")
            slots = ""
            if roles:
                reference_slots.record(ws, name, roles, whats)
                slots = (
                    "\nreference slots — the user fills them in the References panel; comfy_run "
                    "uploads and wires them, and REFUSES while any is EMPTY (do not ask for "
                    "uploads in prose, do not wait — validate now, run when they are filled):\n"
                    + reference_slots.describe(ws, roles, whats)
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

    def _ui_graph(self, nodes: list, api: dict) -> dict:
        """The importable shape, laid out as a plain grid.

        Links are numbered as they are discovered and recorded in three places, because
        litegraph reads all three: the top-level `links` table, the target's `inputs[].link`,
        and the source's `outputs[].links`. A file that fills only the table opens with every
        wire missing.
        """
        order = {str(n.get("id")): i for i, n in enumerate(nodes)}
        ui_nodes = []
        links: list = []
        link_id = 0
        outs: dict = {nid: {} for nid in api}

        for nid, entry in api.items():
            i = order.get(nid, 0)
            inputs_meta = []
            widgets: list = []
            for field, value in entry["inputs"].items():
                link = WorkflowLink.from_input(value, api)
                if link is not None:
                    link_id += 1
                    src, slot = link.node_id, link.output_slot
                    links.append([link_id, src, slot, nid, len(inputs_meta), ""])
                    inputs_meta.append({"name": field, "type": "*", "link": link_id})
                    outs.setdefault(src, {}).setdefault(slot, []).append(link_id)
                else:
                    # A literal is a WIDGET, and widget values are positional — order here is
                    # the order the author listed the inputs, which is the only ordering
                    # information available without asking the instance.
                    widgets.append(value)
            ui_nodes.append(
                {
                    "id": nid,
                    "type": entry["class_type"],
                    "pos": [80 + (i % 5) * 340, 80 + (i // 5) * 300],
                    "size": [300, 200],
                    "flags": {},
                    "order": i,
                    "mode": 0,
                    "inputs": inputs_meta,
                    "outputs": [],
                    "properties": {"Node name for S&R": entry["class_type"]},
                    "widgets_values": widgets,
                    "title": (entry.get("_meta") or {}).get("title", entry["class_type"]),
                }
            )

        by_id = {n["id"]: n for n in ui_nodes}
        for src, slots in outs.items():
            node = by_id.get(src)
            if not node:
                continue
            for slot in sorted(slots):
                while len(node["outputs"]) <= slot:
                    node["outputs"].append(
                        {"name": "", "type": "*", "links": [], "slot_index": len(node["outputs"])}
                    )
                node["outputs"][slot]["links"] = list(slots[slot])

        return {
            "last_node_id": max((int(n) for n in api if str(n).isdigit()), default=len(api)),
            "last_link_id": link_id,
            "nodes": ui_nodes,
            "links": links,
            "groups": [],
            "config": {},
            "extra": {},
            "version": 0.4,
        }

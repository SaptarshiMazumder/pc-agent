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

import json
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_workspace

#: Where workflows land. Inside the run's workspace — never computed from __file__, which is
#: where the plugin lives rather than where this user's files go.
_SUBDIR = "workflows"


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
        "it handles ids, links and layout. Use names that comfy_inventory confirmed exist."
    )
    parameters = {
        "type": "object",
        "required": ["name", "nodes"],
        "properties": {
            "name": {"type": "string", "description": "Workflow name, e.g. 'flux-portrait'."},
            "nodes": {
                "type": "array",
                "description": (
                    "The graph, in order. Each entry: {id, class_type, inputs}. An input value "
                    "is either a literal, or a link written as [upstream_id, output_slot] — the "
                    "same shape ComfyUI's API format uses."
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

            # Every link must point at a node that exists — the one structural error worth
            # catching here, because the server's version of it arrives after a round trip.
            for nid, entry in api.items():
                for field, value in entry["inputs"].items():
                    if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                        if value[0] not in api:
                            return ToolResult.text(
                                f"node {nid}.{field} links to node {value[0]}, which is not in "
                                f"this workflow",
                                is_error=True,
                            )

            ui = self._ui_graph(nodes, api)

            root = Path(current_workspace(".") or ".") / _SUBDIR
            root.mkdir(parents=True, exist_ok=True)
            api_path = root / f"{name}.api.json"
            ui_path = root / f"{name}.json"
            api_path.write_text(json.dumps(api, indent=2) + "\n", encoding="utf-8")
            ui_path.write_text(json.dumps(ui, indent=2) + "\n", encoding="utf-8")

            note = str(params.get("note") or "").strip()
            return ToolResult.text(
                f"wrote {len(api)} nodes"
                + (f" — {note}" if note else "")
                + f"\n  run this:    {api_path}"
                + f"\n  import this: {ui_path}"
                + "\nRun it with comfy_run before calling it finished — a workflow that was "
                "written has not yet been shown to work.",
                details={"api": str(api_path), "ui": str(ui_path), "nodes": len(api)},
                artifacts=[str(api_path), str(ui_path)],
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
                if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                    link_id += 1
                    src, slot = str(value[0]), int(value[1])
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

"""WorkflowFileWriter — one design, written out in both of ComfyUI's JSON shapes, in this chat's folder.

WHY BOTH. `POST /prompt` accepts ONLY the API shape; the browser imports only the UI shape. An
agent that writes one of them leaves the user either unable to run it or unable to open it. So
every design is written as:

    workflows/<chat>/<name>.api.json   what comfy_run submits
    workflows/<chat>/<name>.json       what the user drags into their ComfyUI

GENERATING BOTH IS TRACTABLE; CONVERTING BETWEEN THEM IS NOT. Going UI -> API means replaying
litegraph semantics — positional `widgets_values`, the extra element `control_after_generate`
injects, muted and bypassed nodes that must be elided with their links rewired. Going the other
way from a design we hold is arithmetic, because we already know every field's name.

THE UI FILE IS A CONVENIENCE, AND SAYS SO. Its layout is a plain left-to-right grid (a pipeline's
stages each in a titled group). Widget values are laid out as each node declares them
(NodeWidgetOrder, from the node list) — the editor reads them by position. If the two files ever
disagree, the API file is the one that ran.

Shared by comfy_emit (a graph the agent wrote node by node) and the pipeline tools (a stage built
from a knowledge-base recipe): one writer, so a stage is an ordinary workflow to everything that
runs, lists, validates or saves workflows.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import chat_paths
import reference_slots
import studio_state
from node_widget_order import NodeWidgetOrder
from workflow_installer_exporter import WorkflowInstallerExporter
from workflow_link import WorkflowLink


@dataclass(frozen=True)
class WrittenWorkflow:
    name: str
    api_rel: str  # workspace-relative posix — the only form that survives the sandbox boundary
    ui_rel: str
    digest: str  # the graph's fingerprint: a changed graph must read as a new result
    roles: list[str]
    slots_text: str  # the slot listing for a tool result ('' when the graph has none)
    installer_note: str


class WorkflowFileWriter:
    def __init__(self, workspace: Path, catalogue: dict) -> None:
        self._ws = Path(workspace)
        #: The node list (object_info) the UI file's widget positions come from.
        self._widgets = NodeWidgetOrder(catalogue)

    def write(self, name: str, api: dict, whats: dict[str, str] | None = None,
              order: list[str] | None = None, forget_fixed_shape: bool = False,
              fed_by: dict[str, str] | None = None, subfolder: str = "") -> WrittenWorkflow:
        """Write `api` as `<name>.api.json` + `<name>.json`, record its reference slots and mark
        the design as existing. `order` is the node order for the UI layout (default: graph order).
        `subfolder` puts the pair one level down in the chat's folder (a pipeline's stages)."""
        folder = chat_paths.chat_rel(chat_paths.WORKFLOWS) + (f"/{subfolder}" if subfolder else "")
        api_path, ui_path, installer_note = self.write_files(folder, name, api, order)

        # THE DESIGN NOW EXISTS, which is what unlocks comfy_inventory — see studio_state.
        try:
            studio_state.mark_emitted()
            # And WHEN this name first existed here — what decides whether a checkpoint presented
            # later covers it (studio_state.checkpoint_answered).
            studio_state.mark_first_emit(name)
            # A rewritten template step is a new design: it goes through the ask again.
            studio_state.forget_template_step(name)
            # The user asked for it to change: an ordinary design from here on.
            if forget_fixed_shape:
                studio_state.forget_fixed_shape(name)
        except Exception:  # noqa: BLE001 — a telemetry miss must not fail the write
            pass

        roles = list(reference_slots.roles_in(api))
        slots_text = ""
        if roles:
            reference_slots.record(self._ws, name, roles, whats or {}, fed_by)
            slots_text = reference_slots.describe(self._ws, roles, whats or {})
        digest = hashlib.sha1(json.dumps(api, sort_keys=True).encode("utf-8")).hexdigest()[:8]
        return WrittenWorkflow(name, f"{folder}/{api_path.name}", f"{folder}/{ui_path.name}", digest,
                               roles, slots_text, installer_note)

    def write_files(self, folder: str, name: str, api: dict, order: list[str] | None = None,
                    groups: list[tuple[str, list[str]]] | None = None) -> tuple[Path, Path, str]:
        """Only the two files, `<folder>/<name>.api.json` + `.json` — no slot record, no design
        marks. What a pipeline's ONE combined workflow is written with: it is the download, not
        something the agent runs. Returns (api path, ui path, installer note)."""
        root = self._ws / folder
        root.mkdir(parents=True, exist_ok=True)
        api_path, ui_path = root / f"{name}.api.json", root / f"{name}.json"
        installer_note = ""
        try:
            WorkflowInstallerExporter.invalidate(api_path)
        except OSError:
            installer_note = "\nOld installer invalidation failed; download a fresh installer after validation."
        api_path.write_text(json.dumps(api, indent=2) + "\n", encoding="utf-8")
        ui_path.write_text(json.dumps(self.ui_graph(api, order, groups), indent=2) + "\n", encoding="utf-8")
        return api_path, ui_path, installer_note

    def ui_graph(self, api: dict, order: list[str] | None = None,
                 groups: list[tuple[str, list[str]]] | None = None) -> dict:
        """The importable shape, laid out as a plain grid — or, with `groups` ((title, node ids)
        each, left to right), one titled group per stage with its nodes in a grid inside it.

        Links are numbered as they are discovered and recorded in three places, because litegraph
        reads all three: the top-level `links` table, the target's `inputs[].link`, and the
        source's `outputs[].links`. A file that fills only the table opens with every wire missing.
        """
        place, ui_groups = self._layout(api, order, groups)
        ui_nodes = []
        links: list = []
        link_id = 0
        outs: dict = {nid: {} for nid in api}

        for i, (nid, entry) in enumerate(api.items()):
            inputs_meta = []
            for field, value in entry["inputs"].items():
                link = WorkflowLink.from_input(value, api)
                if link is not None:
                    link_id += 1
                    src, slot = link.node_id, link.output_slot
                    links.append([link_id, src, slot, nid, len(inputs_meta), ""])
                    inputs_meta.append({"name": field, "type": "*", "link": link_id})
                    outs.setdefault(src, {}).setdefault(slot, []).append(link_id)
            ui_nodes.append(
                {
                    "id": nid,
                    "type": entry["class_type"],
                    "pos": place.get(nid, [80, 80]),
                    "size": [300, 200],
                    "flags": {},
                    "order": i,
                    "mode": 0,
                    "inputs": inputs_meta,
                    "outputs": [],
                    "properties": {"Node name for S&R": entry["class_type"]},
                    "widgets_values": self._widgets.widgets(entry["class_type"], entry["inputs"]),
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
            "groups": ui_groups,
            "config": {},
            "extra": {},
            "version": 0.4,
        }

    @staticmethod
    def _layout(api: dict, order: list[str] | None,
                groups: list[tuple[str, list[str]]] | None) -> tuple[dict[str, list[int]], list[dict]]:
        """({node id: [x, y]}, litegraph groups). Without groups: one grid, 5 nodes a row. With
        them: each group is a band of 3 columns, the next group to its right."""
        col_w, row_h = 340, 300
        if not groups:
            ids = order or list(api)
            return {nid: [80 + (i % 5) * col_w, 80 + (i // 5) * row_h] for i, nid in enumerate(ids)}, []
        place: dict[str, list[int]] = {}
        ui_groups = []
        x0 = 80
        for title, ids in groups:
            ids = [n for n in ids if n in api]
            cols = min(3, max(1, len(ids)))
            for i, nid in enumerate(ids):
                place[nid] = [x0 + (i % cols) * col_w, 120 + (i // cols) * row_h]
            rows = max(1, -(-len(ids) // cols))
            width = cols * col_w
            ui_groups.append({"title": title, "bounding": [x0 - 30, 40, width + 20, rows * row_h + 60],
                              "color": "#3f789e", "font_size": 24, "flags": {}})
            x0 += width + 120
        return place, ui_groups


__all__ = ["WorkflowFileWriter", "WrittenWorkflow"]

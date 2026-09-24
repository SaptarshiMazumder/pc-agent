"""library_use — bring a Library item into THIS chat, after which every comfy tool works on it
unchanged.

A WORKFLOW BECOMES ONE OF THIS CHAT'S WORKFLOWS: its api.json (and editor json, when saved with
it) are copied into `workflows/<chat>/` under a role name, its slots are recorded exactly as
comfy_emit records them, and the design is marked as existing — so comfy_validate, comfy_price,
the ask and comfy_run see it as if it had been emitted here. Nothing about the protocol changes:
a copied workflow whose model the user wants swapped still goes research → emit → validate →
price → ask → run.

A REFERENCE IS COPIED BY THE WINDOW, NOT HERE. The sandbox is handed the Library's catalogue and
workflows and NOT its media (plugin.toml), so a saved face or product photo never rides along on
a node search — which also means this tool has no bytes to copy. It answers with the copy it
wants (`details.copy`: from, to) and the window performs it through the daemon's
`workspace.copy`, the same division comfy_delete uses: the tool decides, the host acts. The
slot then reads as filled like any other.

A FILE that is a ComfyUI graph is a workflow the user did not label as one, and is treated as
one; any other file has nothing to bring — it is read with library_read.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_workspace

import chat_paths
import library_paths
import reference_slots
import studio_state
from library_index import LibraryIndex, LibraryItem
from workflow_summary import WorkflowSummary

_UNSAFE = re.compile(r"[^a-z0-9_-]+")


def _slug(name: str) -> str:
    return _UNSAFE.sub("-", (name or "").strip().lower()).strip("-") or "workflow"


class LibraryUseTool(Tool):
    name = "library_use"
    label = "Bring a Library item into this chat"
    default_retryable = True
    description = (
        "Bring a Library item into this chat. A workflow is copied into this chat's workflows "
        "under a role name (`as`, default its Library name) and becomes this chat's workflow: "
        "validate it, price it, ask, run — the normal protocol. A reference (image/video) fills "
        "the slot you name in `as` (the window copies the file); it then counts as filled. "
        "A file that is a ComfyUI graph is treated as a workflow; other files are only read "
        "(library_read)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "item": {"type": "string", "description": "The item's id or name (library_find)."},
            "as": {
                "type": "string",
                "description": (
                    "For a workflow: the role name it gets in this chat (stills, video …); "
                    "default the item's name. For a reference: the slot role it fills "
                    "(model, garment …) — required."
                ),
            },
            "version": {
                "type": "integer",
                "description": "A workflow version (v1, v2 …). Default: the latest.",
            },
        },
        "required": ["item"],
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        ws = Path(current_workspace(".") or ".")
        idx = LibraryIndex.load(ws)
        if idx.problem:
            return ToolResult.text(f"library_use: {idx.problem}", is_error=True)
        item, why = idx.resolve(str(params.get("item") or ""))
        if item is None:
            return ToolResult.text(f"library_use: {why}", is_error=True)
        as_ = str(params.get("as") or "").strip()
        version = params.get("version")
        files, problem = idx.files_of(item, int(version) if version else None)
        if problem:
            return ToolResult.text(f"library_use: {problem}", is_error=True)
        try:
            if item.kind == "reference":
                return self._reference(ws, item, files[0], as_)
            if item.kind == "workflow":
                api = idx.api_graph_file(files)
                if api is None:
                    return ToolResult.text(
                        f"library_use: {item.name} has no .api.json in this version, and only "
                        "API format can be run. Read it with library_read; ask the user for the "
                        "API export if they want it run (never convert by hand).",
                        is_error=True,
                    )
                return self._workflow(ws, item, api, idx.ui_graph_file(files), as_)
            # A file: a graph in disguise, or nothing to bring.
            p = files[0]
            if p.suffix == ".json":
                summary = WorkflowSummary.from_text(p.read_text(encoding="utf-8", errors="replace"))
                if summary is not None and summary.format == "api":
                    return self._workflow(ws, item, p, None, as_)
                if summary is not None and summary.format == "ui":
                    return ToolResult.text(
                        f"library_use: {p.name} is an EDITOR-format graph; only API format can "
                        "be run. Read it with library_read and ask the user for the API export.",
                        is_error=True,
                    )
            return ToolResult.text(
                f"library_use: {item.name} is a file, not a workflow or a reference — there is "
                "nothing to bring into the chat. Read it with library_read."
            )
        except OSError as e:
            return ToolResult.text(f"library_use failed: {e}", is_error=True)

    # ------------------------------------------------------------------ kinds

    def _workflow(self, ws: Path, item: LibraryItem, api_src: Path, ui_src: Path | None, as_: str):
        name = _slug(as_ or item.name)
        try:
            api = json.loads(api_src.read_text(encoding="utf-8"))
        except ValueError as e:
            return ToolResult.text(f"library_use: {api_src.name} is not valid JSON: {e}", is_error=True)
        if WorkflowSummary(api).format != "api":
            return ToolResult.text(
                f"library_use: {api_src.name} is not an API-format graph.", is_error=True
            )
        folder = chat_paths.chat_rel(chat_paths.WORKFLOWS)
        dest = ws / folder
        dest.mkdir(parents=True, exist_ok=True)
        api_path = dest / f"{name}.api.json"
        shutil.copyfile(api_src, api_path)
        ui_rel = ""
        if ui_src is not None:
            ui_path = dest / f"{name}.json"
            shutil.copyfile(ui_src, ui_path)
            ui_rel = f"{folder}/{ui_path.name}"
        api_rel = f"{folder}/{api_path.name}"
        # THE DESIGN NOW EXISTS, exactly as after comfy_emit: inventory unlocks, and the
        # checkpoint bookkeeping knows when this name first appeared here.
        try:
            studio_state.mark_emitted()
            studio_state.mark_first_emit(name)
        except Exception:  # noqa: BLE001 — bookkeeping must not fail the copy
            pass
        roles = list(reference_slots.roles_in(api))
        slots = ""
        if roles:
            reference_slots.record(ws, name, roles, {})
            slots = (
                "\nreference slots — the user fills them in the References panel or from the "
                "Library (library_use a reference with as=<role>); comfy_run REFUSES while any "
                "is EMPTY:\n" + reference_slots.describe(ws, roles, {})
            )
        origin = f"{item.name} v{item.latest_version}" if item.kind == "workflow" else item.name
        return ToolResult.text(
            f"brought {origin} into this chat as workflow '{name}'"
            + f"\n  run this:    {api_rel}"
            + (f"\n  import this: {ui_rel}" if ui_rel else "\n  (no editor json was saved with it)")
            + slots
            + "\nIt is this chat's workflow now: to change it, re-emit it under the same name "
            "with comfy_emit; then comfy_validate, comfy_price and the ask as for any design.",
            details={"workflow": name, "api": api_rel, "ui": ui_rel, "item": item.id},
        )

    def _reference(self, ws: Path, item: LibraryItem, src: Path, role: str):
        role = role.lstrip(reference_slots.TOKEN).strip()
        if not role:
            return ToolResult.text(
                f"library_use: a reference fills a SLOT — say which: "
                f"library_use(item='{item.id}', as='model').",
                is_error=True,
            )
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", role):
            return ToolResult.text(f"library_use: '{role}' is not a slot role name.", is_error=True)
        # A SLOT IS A ROLE A WORKFLOW DECLARES, not a name for the file. Once this chat has
        # declared any (an emit or a library_use recorded them), a reference can only fill one
        # of those — filling "@face" when the graph loads "@model" leaves the real slot empty and
        # comfy_run refusing. Before anything is declared the role is taken as given: the ask
        # may come first, and the workflow that names the slot after it.
        declared = sorted(reference_slots.declared(ws))
        if declared and role not in declared:
            return ToolResult.text(
                f"library_use: '@{role}' is not a slot this chat's workflows declare. The slots "
                f"are: {', '.join('@' + r for r in declared)}. Name the one this reference fills "
                f"(library_use(item='{item.id}', as='{declared[0]}')) — the slot is the graph's "
                "role, not the file's name.",
                is_error=True,
            )
        to = f"{chat_paths.chat_rel(chat_paths.REFERENCES)}/{role}{src.suffix.lower()}"
        frm = library_paths.library_rel(item.path)
        return ToolResult.text(
            f"{item.name} → slot @{role}: the window copies {frm} to {to}. The slot reads as "
            "filled once it has; comfy_run uploads and wires it like any other slot file. If "
            "the References panel still shows it EMPTY, the user can drop it there from the "
            "Library tab.",
            details={"copy": [{"from": frm, "to": to, "role": role}], "item": item.id},
        )

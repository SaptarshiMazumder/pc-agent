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

AN EDITOR-ONLY WORKFLOW IS CONVERTED BY THE MACHINE'S OWN COMFYUI (EditorGraphConverter) — what
most tutorials ship is the editor save, and a run needs the API format. The machine can only
convert nodes it has, so a workflow needing node packs Comfy Cloud lacks names them — Comfy
Cloud runs only its preinstalled packs, so such a workflow cannot run there as it is.
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
from editor_graph_converter import EditorGraphConverter
from fixed_design_shape import FixedDesignShape
from library_index import LibraryIndex, LibraryItem
from workflow_reference_repository import WorkflowReferenceRepository
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
        "(library_read). A workflow saved only in EDITOR format is converted by the machine's "
        "ComfyUI; if it needs node packs Comfy Cloud lacks, this names them — Comfy Cloud runs "
        "only its preinstalled packs, so it cannot run there as it is."
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

    def __init__(self, converter=None) -> None:
        #: () -> EditorGraphConverter | None: the machine's ComfyUI, or None when there is none.
        self._converter = converter or (lambda: None)

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
                ui = idx.ui_graph_file(files)
                if api is None and ui is not None:
                    return self._from_editor(ws, item, ui, as_)
                if api is None:
                    return ToolResult.text(
                        f"library_use: {item.name} has no workflow graph in this version.",
                        is_error=True,
                    )
                return self._workflow(ws, item, api, ui, as_)
            # A file: a graph in disguise, or nothing to bring.
            p = files[0]
            if p.suffix == ".json":
                summary = WorkflowSummary.from_text(p.read_text(encoding="utf-8", errors="replace"))
                if summary is not None and summary.format == "api":
                    return self._workflow(ws, item, p, None, as_)
                if summary is not None and summary.format == "ui":
                    return self._from_editor(ws, item, p, as_)
            return ToolResult.text(
                f"library_use: {item.name} is a file, not a workflow or a reference — there is "
                "nothing to bring into the chat. Read it with library_read."
            )
        except OSError as e:
            return ToolResult.text(f"library_use failed: {e}", is_error=True)

    # ------------------------------------------------------------------ kinds

    def _from_editor(self, ws: Path, item: LibraryItem, ui_src: Path, as_: str):
        """An editor-only workflow, converted by the machine's ComfyUI — or the node packs it
        needs first, named and recorded so they may be installed."""
        name = _slug(as_ or item.name)
        converter = self._converter()
        if converter is None:
            return ToolResult.text(
                f"library_use: {item.name} is saved in EDITOR format; the machine's ComfyUI converts "
                "it for running, and there is no machine yet. Once one is connected, call "
                "library_use again.",
                is_error=True,
            )
        try:
            ui = json.loads(ui_src.read_text(encoding="utf-8"))
            missing = converter.missing_classes(ui)
            if missing:
                # WHAT THE USER'S WORKFLOW NEEDS is the evidence for the packs, exactly as a
                # validation's unknown classes are: recorded under this workflow's name.
                studio_state.mark_first_emit(name)
                studio_state.mark_validated(name, [], missing)
                return ToolResult.text(
                    f"library_use: {item.name} is saved in EDITOR format and uses node types this "
                    "ComfyUI does not have: " + ", ".join(missing) + ". Comfy Cloud runs only its "
                    "preinstalled node packs, so this workflow cannot run there as it is — tell the "
                    "user which nodes are missing.",
                    is_error=True,
                )
            api, dropped = converter.convert(ui)
        except ValueError as e:
            return ToolResult.text(f"library_use: {item.name}: {e}", is_error=True)
        result = self._workflow(ws, item, api, ui_src, as_)
        if dropped and not result.is_error:
            note = ("\nConverted by the machine's ComfyUI. Dropped links into inputs the installed "
                    "node packs no longer have (their pack changed since the workflow was made — "
                    "ComfyUI's editor drops these the same way): " + "; ".join(dropped)
                    + ". Say so to the user in one line.")
            result = ToolResult.text(result.content[0].text + note, details=result.details)
        return result

    def _workflow(self, ws: Path, item: LibraryItem, api_src, ui_src: Path | None, as_: str):
        """`api_src`: the API graph's file, or the graph itself (converted from the editor save)."""
        name = _slug(as_ or item.name)
        if isinstance(api_src, dict):
            api = api_src
        else:
            try:
                api = json.loads(api_src.read_text(encoding="utf-8"))
            except ValueError as e:
                return ToolResult.text(f"library_use: {api_src.name} is not valid JSON: {e}", is_error=True)
        if WorkflowSummary(api).format != "api":
            return ToolResult.text(
                f"library_use: {getattr(api_src, 'name', item.name)} is not an API-format graph.", is_error=True
            )
        folder = chat_paths.chat_rel(chat_paths.WORKFLOWS)
        dest = ws / folder
        dest.mkdir(parents=True, exist_ok=True)
        api_path = dest / f"{name}.api.json"
        api_path.write_text(json.dumps(api, indent=2), encoding="utf-8")
        # THE USER'S OWN WORKFLOW PROVES ITS OWN MODEL FILES — see WorkflowReferenceRepository.
        WorkflowReferenceRepository(ws, fetch=None).remember(api, f"library:{item.id}")
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
            studio_state.forget_template_step(name)
            studio_state.mark_fixed_design()
        except Exception:  # noqa: BLE001 — bookkeeping must not fail the copy
            pass
        # Not swallowed: without the shape a re-emit could rewrite the user's design unnoticed.
        studio_state.mark_fixed_shape(name, FixedDesignShape.of(api).to_json())
        roles = list(reference_slots.roles_in(api))
        slots = ""
        if roles:
            reference_slots.record(ws, name, roles, {})
            slots = (
                "\nreference slots — the user fills them in the References panel or from the "
                "Library (library_use a reference with as=<role>); comfy_run REFUSES while any "
                "is EMPTY:\n" + reference_slots.describe(ws, roles, {})
            )
        # A LOADER STILL READING THE WORKFLOW'S OWN EXAMPLE FILE is where the user's file goes. The
        # agent, told of no slot, once decided the user's start frame was "missing" while it sat in
        # this chat's references, and asked for it three times. So: which loaders, which files.
        examples = [
            f"node {nid} {e.get('class_type')}.{field} = {value!r}"
            for nid, e in api.items() if isinstance(e, dict) and str(e.get("class_type") or "").startswith("Load")
            for field, value in (e.get("inputs") or {}).items()
            if field in reference_slots._SLOT_FIELDS and isinstance(value, str) and reference_slots.role_of(value) is None
        ]
        refs_dir = reference_slots.folder(ws)
        have = sorted(p.name for p in refs_dir.iterdir() if p.is_file() and not p.name.startswith(".")) if refs_dir.is_dir() else []
        if examples:
            slots += (
                "\nloaders reading the workflow's OWN example file — to feed the user's file, set it to "
                "'@<role>' and re-emit (an input change; comfy_run uploads and wires it):\n  "
                + "\n  ".join(examples)
                + "\nfiles already in this chat's references: " + (", ".join(have) if have else "none")
            )
        origin = f"{item.name} v{item.latest_version}" if item.kind == "workflow" else item.name
        return ToolResult.text(
            f"brought {origin} into this chat as workflow '{name}'"
            + f"\n  run this:    {api_rel}"
            + (f"\n  import this: {ui_rel}" if ui_rel else "\n  (no editor json was saved with it)")
            + slots
            + "\nIt is this chat's workflow now, and its model files are proven by it: "
            "comfy_validate it WITHOUT reference_workflow_url and install what validate lists. "
            "It runs AS IT IS: its nodes and wiring stay. To set an input (prompt, image, size, "
            "length, a value the machine does not offer), re-emit it under the same name with "
            "every node and link unchanged — comfy_emit refuses anything else. Only a change the "
            "USER asked for rewires it (comfy_emit with user_asked = their words).",
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

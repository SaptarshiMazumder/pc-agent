"""library_read — one Library item, read.

A WORKFLOW IS READ AS A SUMMARY FIRST (workflow_summary): its nodes, links, slots and the
models it names — and the raw api.json beside it when it is small enough for an edit to copy
exact keys from. A FILE is read as text, capped. A REFERENCE is never opened: the agent does not
receive pixels (rule 6), so the answer is its name and size and the way to use it.

THIS IS WHAT "READ THE JSON I ATTACHED" NOW MEANS. There is no generic read tool here on
purpose (agent.toml), and there does not need to be: everything the agent has business reading
is either this chat's own workflow, which it wrote, or something the user chose to keep.
"""

from __future__ import annotations

import json
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_workspace

import library_paths
from library_index import LibraryIndex, LibraryItem
from workflow_summary import WorkflowSummary

#: A text file bigger than this is cut; the first part is shown with the cut named.
TEXT_CAP = 200 * 1024
#: An api.json bigger than this travels as its summary only.
GRAPH_CAP = 1024 * 1024


def _read_text(p: Path, cap: int) -> tuple[str, bool]:
    """(text, was_cut). A file that is not UTF-8 text answers ('', False) with a note by caller."""
    raw = p.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return "", False
    if len(text) > cap:
        return text[:cap], True
    return text, False


class LibraryReadTool(Tool):
    name = "library_read"
    label = "Read a Library item"
    default_retryable = True
    description = (
        "Read one Library item by id or name (from library_find). A workflow: every node, what "
        "feeds it, the slots it declares (@model …) and the models it names, plus the raw "
        "api.json when small enough to edit from. A file: its text. A reference: its name and "
        "size only (you never see pixels). Reading is not using — to edit or run a workflow, "
        "library_use it first so it becomes this chat's."
    )
    parameters = {
        "type": "object",
        "properties": {
            "item": {"type": "string", "description": "The item's id or name."},
            "version": {
                "type": "integer",
                "description": "A workflow version (v1, v2 …). Default: the latest.",
            },
        },
        "required": ["item"],
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        root = Path(current_workspace(".") or ".")
        idx = LibraryIndex.load(root)
        if idx.problem:
            return ToolResult.text(f"library_read: {idx.problem}", is_error=True)
        item, why = idx.resolve(str(params.get("item") or ""))
        if item is None:
            return ToolResult.text(f"library_read: {why}", is_error=True)
        version = params.get("version")
        files, problem = idx.files_of(item, int(version) if version else None)
        if problem:
            return ToolResult.text(f"library_read: {problem}", is_error=True)
        if item.kind == "workflow":
            return self._workflow(idx, item, files, int(version or item.latest_version or 0))
        if item.kind == "reference":
            p = files[0]
            return ToolResult.text(
                f"{item.name}: a {item.kind} — {p.name}, {p.stat().st_size} bytes"
                + (f", note: {item.note}" if item.note else "")
                + ". You do not receive its pixels. To use it as workflow input: "
                f"library_use(item='{item.id}', as='<role>') fills that slot."
            )
        return self._file(item, files[0])

    # ------------------------------------------------------------------ kinds

    def _workflow(self, idx: LibraryIndex, item: LibraryItem, files: list[Path], v: int):
        api = idx.api_graph_file(files)
        ui = idx.ui_graph_file(files)
        head = (
            f"{item.name} v{v} ({item.origin}"
            + (f", from: {item.from_chat['title']}" if item.from_chat.get("title") else "")
            + f") — files: {', '.join(p.name for p in files)}"
            + (f"\nnote: {item.note}" if item.note else "")
        )
        if api is not None:
            text, cut = _read_text(api, GRAPH_CAP)
            summary = WorkflowSummary.from_text(text) if text and not cut else None
            if summary is None:
                return ToolResult.text(
                    f"{head}\n{api.name} is not readable JSON or is over "
                    f"{GRAPH_CAP // 1024} KB — it cannot be summarised here.",
                    is_error=True,
                )
            body = summary.text()
            raw = "" if cut else f"\n\nraw {api.name}:\n{text}"
            return ToolResult.text(
                f"{head}\n\n{body}{raw}\n\nTo edit or run it in this chat: "
                f"library_use(item='{item.id}').",
                details={"item": item.__dict__, "version": v, "format": summary.format},
            )
        if ui is not None:
            text, cut = _read_text(ui, GRAPH_CAP)
            summary = WorkflowSummary.from_text(text) if text and not cut else None
            body = summary.text() if summary else f"{ui.name} is not readable JSON."
            return ToolResult.text(
                f"{head}\n\n{body}\n\nThere is no .api.json in this version, so it cannot be "
                "submitted as is.",
                details={"item": item.__dict__, "version": v, "format": "ui"},
            )
        return ToolResult.text(
            f"{head}\nNo .json workflow file in this version — nothing to read.", is_error=True
        )

    def _file(self, item: LibraryItem, p: Path):
        text, cut = _read_text(p, TEXT_CAP)
        if not text:
            return ToolResult.text(
                f"{item.name}: {p.name}, {p.stat().st_size} bytes — not a text file, so there is "
                "nothing to read from it here."
            )
        # A JSON file that is a ComfyUI graph is a workflow the user did not label as one: say
        # so, and read it the workflow way, so "edit the JSON I pasted" works whatever the tab
        # called it.
        if p.suffix == ".json" and not cut:
            summary = WorkflowSummary.from_text(text)
            if summary is not None and summary.format != "unknown":
                return ToolResult.text(
                    f"{item.name}: {p.name} is a ComfyUI workflow ({summary.format} format).\n\n"
                    f"{summary.text()}\n\nraw {p.name}:\n{text}\n\n"
                    f"To edit or run it in this chat: library_use(item='{item.id}').",
                    details={"item": item.__dict__, "format": summary.format},
                )
        note = f"\n\n[cut at {TEXT_CAP // 1024} KB of {p.stat().st_size} bytes]" if cut else ""
        return ToolResult.text(
            f"{item.name}: {p.name} ({p.stat().st_size} bytes)\n\n{text}{note}",
            details={"item": item.__dict__, "cut": cut},
        )

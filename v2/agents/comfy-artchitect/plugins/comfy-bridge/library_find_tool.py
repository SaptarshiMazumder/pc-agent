"""library_find — what is in the user's Library.

THE LIBRARY IS NOT "THE WORKSPACE". Rule 12 forbids opening a new job by reading what other
jobs left behind, and it stays: this tool lists a catalogue the USER curated — things they chose
to keep — and only when the user's own words point at it ("the reel we made", "my saved face",
a file they attached that landed there). It never lists another chat's folders; it cannot.
"""

from __future__ import annotations

from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_workspace

import library_paths
from library_index import LibraryIndex


class LibraryFindTool(Tool):
    name = "library_find"
    label = "Find items in the user's Library"
    default_retryable = True
    description = (
        "List what the user keeps in their Library — saved workflows, reference images and "
        "videos, and files they uploaded — optionally filtered by kind and a word from the name, "
        "note or the chat it came from. Use it when the user refers to something they kept "
        "('the reel we made', 'my jacket workflow', 'the face I uploaded') or attached a file "
        "that is not an image. Each row: id, kind, origin (uploaded by them, or saved from a "
        "chat), name, note, and the chat it came from. Then library_read to read one, "
        "library_use to bring one into this chat."
    )
    parameters = {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": list(library_paths.KINDS),
                "description": "Only this kind: workflow, reference (image/video input), file, or template (a whole saved setup of several workflows — bring it in with template_use).",
            },
            "query": {
                "type": "string",
                "description": "A word from the item's name, note or origin chat. Empty lists all.",
            },
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        root = Path(current_workspace(".") or ".")
        idx = LibraryIndex.load(root)
        if idx.problem:
            return ToolResult.text(f"library_find: {idx.problem}", is_error=True)
        kind = str(params.get("kind") or "").strip()
        query = str(params.get("query") or "").strip()
        items = idx.find(kind=kind, query=query)
        if not items:
            if not idx.items:
                return ToolResult.text(
                    "The Library is empty. The user fills it from the Library tab (upload) or "
                    "with 'Add to Library' / 'Save to Library' on files and workflows in this "
                    "app. Nothing to read from here yet — build fresh."
                )
            what = " ".join(x for x in (kind, f"matching '{query}'" if query else "") if x)
            return ToolResult.text(
                f"no Library item {what}. There are {len(idx.items)} items in all — call "
                "library_find with no filter to see them."
            )
        lines = [f"{len(items)} Library item(s) — id  kind  origin  name — note (from: chat):"]
        lines += [f"  {it.one_line()}" for it in items]
        lines.append(
            "library_read <id> reads one (a workflow's nodes, models and slots; a file's text). "
            "library_use <id> brings a workflow into this chat as one of its workflows, or a "
            "reference into a slot."
        )
        return ToolResult.text(
            "\n".join(lines),
            details={"items": [it.__dict__ for it in items]},
        )

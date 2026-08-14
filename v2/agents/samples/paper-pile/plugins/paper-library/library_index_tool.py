"""LibraryIndexTool — what is in the library, without opening every note."""

from __future__ import annotations

import json

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from library_note_store import LibraryNoteStore


class LibraryIndexTool(Tool):
    name = "library_index"
    label = "Library index"
    default_retryable = True
    description = (
        "List every document in the library — title, source, date added, tags and the first "
        "line of the summary. Use this to answer 'what do I have' without opening each note. "
        "The app calls it to render the list, so it is cheap and safe to call often."
    )
    parameters = {
        "type": "object",
        "required": [],
        "properties": {
            "tag": {"type": "string", "description": "Only documents carrying this tag."},
        },
    }

    def __init__(self, notes: LibraryNoteStore):
        self._notes = notes

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            wanted = str(params.get("tag") or "").strip().lower()
            rows = []
            for path, note in self._notes.all():
                if wanted and wanted not in [t.lower() for t in note["tags"]]:
                    continue
                first = next((ln.strip() for ln in note["body"].splitlines() if ln.strip()), "")
                rows.append(
                    {
                        "file": path.name,
                        "path": str(path),
                        "title": note["title"],
                        "source": note["source"],
                        "added": note["added"],
                        "tags": note["tags"],
                        "summary": first[:300],
                    }
                )
            if not rows:
                where = f" tagged '{wanted}'" if wanted else ""
                return ToolResult.text(
                    f"The library is empty{where}. Drop a PDF, paste a link, or point me at a "
                    f"folder with library_scan."
                )
            # JSON because the APP parses this. A prose list would be friendlier to read and
            # impossible to render.
            return ToolResult.text(json.dumps({"count": len(rows), "documents": rows}, indent=1))
        except Exception as e:  # noqa: BLE001 — an authored tool never crashes the loop
            return ToolResult.text(f"library_index failed: {type(e).__name__}: {e}", is_error=True)

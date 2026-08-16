"""LibrarySearchTool — literal search across the NOTES.

Distinct from `library_ask`, deliberately. This searches what you wrote down, matches the exact
word, and is instant. `library_ask` searches the documents' full text by meaning. "Where is that
note" and "what did the paper actually say" are different questions.
"""

from __future__ import annotations

import json

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from library_note_store import LibraryNoteStore, library_root


class LibrarySearchTool(Tool):
    name = "library_search"
    label = "Search notes"
    default_retryable = True
    description = (
        "Search the full text of your NOTES and return the matching lines with their document. "
        "Exact, literal word matching — use it to find a note you know exists. To search what "
        "the source documents actually say, and by meaning rather than spelling, use library_ask."
    )
    parameters = {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {"type": "string", "description": "Words to look for (case-insensitive)."},
            "max_results": {"type": "integer", "minimum": 1, "description": "Default 40."},
        },
    }

    def __init__(self, notes: LibraryNoteStore):
        self._notes = notes

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            query = str(params.get("query") or "").strip().lower()
            if not query:
                return ToolResult.text("library_search needs a `query`", is_error=True)
            limit = max(1, int(params.get("max_results") or 40))

            notes = self._notes.all()
            hits = []
            for path, note in notes:
                for i, line in enumerate(note["body"].splitlines(), 1):
                    if query in line.lower():
                        hits.append(
                            {
                                "file": path.name,
                                "title": note["title"],
                                "line": i,
                                "text": line.strip()[:300],
                            }
                        )
                        if len(hits) >= limit:
                            break
                if len(hits) >= limit:
                    break

            if not hits:
                return ToolResult.text(
                    f"No NOTE contains {query!r} — {len(notes)} note(s) searched in "
                    f"{library_root()}. The documents themselves may still discuss it: try "
                    f"library_ask, which searches their full text by meaning."
                )
            return ToolResult.text(json.dumps({"count": len(hits), "matches": hits}, indent=1))
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"library_search failed: {type(e).__name__}: {e}", is_error=True)

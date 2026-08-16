"""LibraryInventoryTool — the authoritative list of RAG-indexed source files."""

from __future__ import annotations

import json
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from library_database import LibraryDatabase
from library_note_store import database_path, sources_root


class LibraryInventoryTool(Tool):
    name = "library_inventory"
    label = "Indexed files"
    default_retryable = True
    description = (
        "List every source file whose full text is in the RAG index, including chunk and embedding "
        "coverage. Use this for 'what files are indexed?'. Unlike library_index, this reads the "
        "retrieval database rather than summary notes."
    )
    parameters = {"type": "object", "required": [], "properties": {}}

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            db = LibraryDatabase(database_path())
            db.ensure_schema()
            rows = []
            filed = sources_root()
            for row in db.document_inventory():
                source_path = str(row.get("source_path") or "")
                source_name = Path(source_path).name if source_path else ""
                copies = sorted(filed.glob(f"{row['slug']}.*")) if filed.exists() else []
                chunks = int(row.get("chunks") or 0)
                embedded = int(row.get("embedded_chunks") or 0)
                rows.append(
                    {
                        "slug": row["slug"],
                        "title": row.get("title") or row["slug"].replace("-", " "),
                        "source_path": source_path,
                        "source_name": source_name or (copies[0].name if copies else row["slug"]),
                        "filed_copy": str(copies[0]) if copies else "",
                        "size": int(row.get("size") or 0),
                        "indexed_at": row.get("indexed_at") or "",
                        "chunks": chunks,
                        "embedded_chunks": embedded,
                        "search_mode": "semantic + lexical" if chunks and embedded == chunks else "lexical",
                    }
                )
            return ToolResult.text(json.dumps({"count": len(rows), "files": rows}, indent=1))
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(
                f"library_inventory failed: {type(e).__name__}: {e}", is_error=True
            )

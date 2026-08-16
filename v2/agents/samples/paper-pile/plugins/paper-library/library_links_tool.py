"""LibraryLinksTool — the shape of the library, which a list cannot show.

Thirty notes look identical in a list whether they are densely cross-referenced or thirty
strangers in a folder. This is the view that tells them apart, and it surfaces the two failures
that accumulate silently: a note nothing points at, and a link to a note that was never written.
"""

from __future__ import annotations

import json

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from library_note_store import LibraryNoteStore


class LibraryLinksTool(Tool):
    name = "library_links"
    label = "Library links"
    default_retryable = True
    description = (
        "The link structure of the library: which notes point at which, and which notes nothing "
        "points at. Use it to find ORPHANS — documents filed and never connected to anything — "
        "and to answer 'what relates to this?' without reading every note."
    )
    parameters = {
        "type": "object",
        "required": [],
        "properties": {
            "slug": {
                "type": "string",
                "description": "Only links touching this note (its file name without .md).",
            }
        },
    }

    def __init__(self, notes: LibraryNoteStore):
        self._notes = notes

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            wanted = str(params.get("slug") or "").strip().lower().removesuffix(".md")
            notes = self._notes.all()
            by_slug = {path.stem: note for path, note in notes}

            links: list[dict] = []
            inbound: dict[str, int] = {slug: 0 for slug in by_slug}
            for path, note in notes:
                for target in self._notes.links_in(note["body"]):
                    links.append(
                        {
                            "from": path.stem,
                            "to": target,
                            # A link to a note that does not exist is a typo or a note never
                            # written. Reported rather than dropped: silently ignoring it is how
                            # a library ends up full of dead references nobody notices.
                            "resolves": target in by_slug,
                        }
                    )
                    if target in inbound:
                        inbound[target] += 1

            if wanted:
                links = [x for x in links if x["from"] == wanted or x["to"] == wanted]

            orphans = sorted(
                slug
                for slug in by_slug
                if inbound.get(slug, 0) == 0 and not any(x["from"] == slug for x in links)
            )
            broken = sorted({x["to"] for x in links if not x["resolves"]})
            return ToolResult.text(
                json.dumps({"links": links, "orphans": orphans, "broken": broken}, indent=1)
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"library_links failed: {type(e).__name__}: {e}", is_error=True)

"""list_templates: browse the art-template gallery (the FigureLabs-style style picker).

Returns every art template the agent can use — id, name, what it looks like, and when to reach for
it — read live from the agent's `templates/` folder. The agent calls this to CHOOSE a style before
generate_artwork (which takes `template=<id>`). Because templates are files, this list grows the
moment a new `.toml` is dropped in — nothing here is hardcoded.
"""

from __future__ import annotations

import asyncio

import figure_art_templates as tpl

from agent_runtime.application.interfaces.tool import Tool, ToolResult


class ListTemplatesTool(Tool):
    name = "list_templates"
    plugin = "figure-art"
    description = (
        "List the available ART TEMPLATES (the style gallery) for scientific figures — each is a "
        "curated, art-directed look (e.g. biorender-shaded, flat-vector, ghosted-anatomy, "
        "isometric-3d-stem, watercolor-atlas, cell-journal-cover). Returns id, name, description, "
        "when_to_use, tags, palette, and default aspect/resolution. Pick an id and pass it to "
        "generate_artwork as `template`. Templates are files in the agent's templates/ folder, so the "
        "gallery is user-extensible — this reflects whatever is there now."
    )
    label = "List Templates"
    concurrency = "parallel"
    parameters = {
        "type": "object",
        "properties": {
            "tag": {
                "type": "string",
                "description": "Optional: only templates carrying this tag (e.g. 'medical', 'flat', '3d').",
            },
        },
    }

    def __init__(self, config):
        self.config = config

    def _run(self, params: dict) -> dict:
        items = tpl.catalog(self.config)
        tag = (params.get("tag") or "").strip().lower()
        if tag:
            items = [t for t in items if tag in [str(x).lower() for x in t.get("tags", [])]]
        return {"count": len(items), "templates": items}

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            r = await asyncio.to_thread(self._run, params)
        except Exception as e:
            return ToolResult.text(f"list_templates failed: {e}", is_error=True)
        if not r["templates"]:
            return ToolResult.text(
                "No art templates found. Add *.toml files under the agent's templates/ folder "
                "(see templates/README.md).",
                details=r,
            )
        lines = [f"{len(r['templates'])} art template(s):"]
        for t in r["templates"]:
            extra = " [exemplars]" if t.get("has_exemplars") else ""
            lines.append(f"  • {t['id']} — {t['name']}: {t['description']}{extra}")
            if t.get("when_to_use"):
                lines.append(f"      use when: {t['when_to_use']}")
        lines.append("\nPass an id to generate_artwork as `template` (with `subject`).")
        return ToolResult.text("\n".join(lines), details=r)

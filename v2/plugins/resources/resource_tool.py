"""resource tool — the agent's CRUD-on-resources, on the fly.

One tool, many actions (list/read/create/edit/delete/describe), delegating to the
ResourceManager so every change keeps the cached index + descriptions in sync. Operates
in the CALLING agent's workspace (from the run context), so each agent manages only its
own resources. Reading content still uses the normal `read` tool too; this adds the
described catalog + index-maintaining writes.
"""

from __future__ import annotations

from pathlib import Path

from agentd.application.run_context import current_run_context, current_workspace

from agentd.application.interfaces.tool import Tool, ToolResult

_READ_CAP = 20_000


class ResourceTool(Tool):
    name = "resource"
    concurrency = "sequential"   # create/edit/delete mutate the workspace + index
    description = (
        "Manage your workspace resources (scripts, docs, images, data) with a live, "
        "described index. Actions: list (described catalog) · read · create · edit · "
        "delete · describe (refresh a resource's description) · cleanup (delete throwaway "
        "files in your tmp/ scratch dir). Prefer this over raw file writes for materials you "
        "want catalogued and reused later. Write intermediate/throwaway files under tmp/ "
        "(never indexed) and run cleanup when a task is done."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string",
                       "enum": ["list", "read", "create", "edit", "delete", "describe", "cleanup"]},
            "path": {"type": "string", "description": "workspace-relative path (not for 'list'/'cleanup')."},
            "content": {"type": "string", "description": "file content (for create/edit)."},
        },
        "required": ["action"],
    }

    def __init__(self, manager):
        self._m = manager

    def _ctx(self):
        ctx = current_run_context()
        ws = current_workspace(None)
        agent_id = ctx.agent_id if ctx else "main"
        return ws, agent_id

    async def execute(self, tool_call_id, params, abort, on_update=None):
        action = (params.get("action") or "").strip()
        path = (params.get("path") or "").strip()
        ws, agent_id = self._ctx()
        if ws is None:
            return ToolResult.text("no workspace available for this run", is_error=True)
        try:
            if action == "list":
                manifest = self._m.manifest(ws, agent_id)
                return ToolResult.text(manifest or "(no resources yet)")
            if action == "cleanup":
                deleted = self._m.cleanup(ws, agent_id)   # purge <workspace>/tmp/ scratch
                if not deleted:
                    return ToolResult.text("nothing to clean (tmp/ is empty)")
                return ToolResult.text(
                    f"cleaned {len(deleted)} scratch file(s) from tmp/: " + ", ".join(deleted[:20]))
            if not path:
                return ToolResult.text(f"action '{action}' needs a path", is_error=True)
            if action == "read":
                text = self._m.read(ws, path)
                if len(text) > _READ_CAP:
                    text = text[:_READ_CAP] + "\n…[truncated]"
                return ToolResult.text(text)
            if action == "create":
                r = self._m.create(ws, agent_id, path, params.get("content") or "")
                self._m.enqueue_enrich(ws, agent_id, r)   # expensive describe -> background
                return ToolResult.text(f"created {r.rel_path} ({r.kind}) - {r.description or 'no description'}")
            if action == "edit":
                r = self._m.edit(ws, agent_id, path, params.get("content") or "")
                self._m.enqueue_enrich(ws, agent_id, r)   # re-describe in the background
                return ToolResult.text(f"updated {r.rel_path} - {r.description or 'no description'}")
            if action == "delete":
                ok = self._m.delete(ws, agent_id, path)
                return ToolResult.text(f"deleted {path}" if ok else f"not found: {path}")
            if action == "describe":
                desc = await self._m.describe_rich(ws, agent_id, path)
                return ToolResult.text(f"{path}: {desc or 'no description'}")
            return ToolResult.text(f"unknown action: {action}", is_error=True)
        except FileNotFoundError:
            return ToolResult.text(f"not found: {path}", is_error=True)
        except ValueError as e:
            return ToolResult.text(str(e), is_error=True)

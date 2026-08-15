"""list_workflows — the agent's memory of what it has already built for this user.

An architect who cannot see last week's work rebuilds it from scratch, differently, and the user
gets a subtly different graph every time they ask for "the same thing but bigger". Reusing the
workflow they already approved keeps the sampler, resolution and seed they liked — details that
a rebuild silently discards.
"""

from __future__ import annotations

import json
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_workspace


class ListWorkflowsTool(Tool):
    name = "list_workflows"
    label = "List Workflows"
    default_retryable = True
    description = (
        "Every workflow already built for this user, newest first, with its format and node "
        "count. Check here before building something 'like last time' — revising the graph they "
        "already approved beats rebuilding it from memory."
    )
    parameters = {"type": "object", "properties": {}}

    async def execute(self, tool_call_id, params, abort, on_update=None):
        # THE WORKSPACE IS NOT A FIXED PATH. The runtime picks it per run — a signed-in user has
        # their own, a project chat uses the project's — so it is asked for, never derived from
        # the agent's own directory.
        folder = Path(current_workspace(".")) / "workflows"
        rows = []
        if folder.is_dir():
            for path in sorted(
                folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
            ):
                rows.append(f"- {path.name}  ({_describe(path)})")
        if not rows:
            # NAME THE FOLDER. "No workflows yet" is true and useless: it reads identically
            # whether none were built or one was written somewhere else, and the second case
            # otherwise costs several tool calls to work out.
            return ToolResult.text(
                f"No workflows in {folder}\n"
                f"That is this run's workspace — write new ones there, under workflows/. "
                f"A file saved anywhere else is invisible to this tool AND to the app's "
                f"Workflows tab, which reads the same folder."
            )
        return ToolResult.text(f"{len(rows)} workflow(s) in {folder}:\n" + "\n".join(rows))


def _describe(path: Path) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # A broken file is worth LISTING, loudly — it is probably the one being asked about, and
        # hiding it would look like it was never created.
        return "unreadable / not valid JSON"
    if isinstance(data.get("nodes"), list):
        return f"UI export, {len(data['nodes'])} nodes"
    return f"API format, {len(data)} nodes — runnable"

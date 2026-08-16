"""list_workflows — the agent's memory of what it has already built for this user.

An architect who cannot see last week's work rebuilds it from scratch, differently, and the user
gets a subtly different graph every time they ask for "the same thing but bigger". Reusing the
workflow they already approved keeps the sampler, resolution and seed they liked — details that
a rebuild silently discards.

TWO AUDIENCES, TWO CHANNELS, and this is the part worth copying.

  content   prose, for the MODEL: a sentence and a bulleted list it can reason about.
  details   the same facts as data, for a PROGRAM: the app's Workflows panel renders from this.

They are not redundant. A window given only the text has to parse it, and this tool's text is a
message written for a reader — "3 workflow(s) in C:\\…\\workflows:" and some bullets. That is not
an API. It was parsed with a regex for exactly one afternoon, until a reworded line made the panel
render "Nothing built yet" over a folder with two files in it, with no error anywhere.

`artifacts` is deliberately NOT used. That channel means "files THIS tool produced and wants shown
to the user" — a listing tool that fills it can surface any file it happens to find, which is the
rule the field exists to keep.
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
        found = [_read(p) for p in _json_files(folder)]
        details = {"folder": str(folder), "workflows": found}

        if not found:
            # NAME THE FOLDER. "No workflows yet" is true and useless: it reads identically
            # whether none were built or one was written somewhere else, and the second case
            # otherwise costs several tool calls to work out.
            return ToolResult.text(
                f"No workflows in {folder}\n"
                f"That is this run's workspace — write new ones there, under workflows/. "
                f"A file saved anywhere else is invisible to this tool AND to the app's "
                f"Workflows tab, which reads the same folder.",
                details=details,
            )

        rows = "\n".join(f"- {w['name']}  ({_describe(w)})" for w in found)
        return ToolResult.text(f"{len(found)} workflow(s) in {folder}:\n{rows}", details=details)


def _json_files(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)


def _read(path: Path) -> dict:
    """One workflow, as facts. `format` is '' when the file will not parse — which is worth
    REPORTING rather than hiding: it is usually the one being asked about, and omitting it would
    look like it was never created."""
    entry = {"name": path.name, "path": str(path), "format": "", "nodes": 0, "runnable": False}
    try:
        entry["modified"] = path.stat().st_mtime
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return entry
    if isinstance(data.get("nodes"), list):
        entry.update(format="ui", nodes=len(data["nodes"]))
    elif isinstance(data, dict):
        # The flat {id: node} form is the only one /prompt accepts, so it is the only one that
        # can actually be RUN — a distinction the panel puts a button on.
        entry.update(format="api", nodes=len(data), runnable=True)
    return entry


def _describe(entry: dict) -> str:
    if not entry["format"]:
        return "unreadable / not valid JSON"
    if entry["format"] == "ui":
        return f"UI export, {entry['nodes']} nodes"
    return f"API format, {entry['nodes']} nodes — runnable"

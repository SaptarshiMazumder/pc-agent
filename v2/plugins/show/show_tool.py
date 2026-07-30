"""show_files — the universal "here are my deliverables" tool.

Producing tools (figures, video, slides, diagrams, tts, …) already declare their own
outputs. This is the GENERIC escape hatch: any agent that creates a file some other way
(e.g. `main` writing a chart via code) calls show_files to display it, or re-displays a
file the user asks to see again. It is the only generic path by which a file becomes a
rendered deliverable — nothing is inferred from text, so a file merely found / read /
listed is never shown. Decoupled: the tool just resolves the paths and hands them to the
deliverable channel (ToolResult.artifacts).
"""

from __future__ import annotations

from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_workspace


class ShowFilesTool(Tool):
    name = "show_files"
    label = "Show Files"
    description = (
        "Show the finished deliverable FILE(S) you produced so the user sees them — "
        "images/video/audio render inline, documents (pdf/pptx/docx/…) show as an openable "
        "card. Pass the paths of FINAL outputs you created (relative to your workspace or "
        "absolute). Do NOT pass intermediate/scratch files, and do NOT pass files you only "
        "read, searched, or listed — only what you actually produced for the user."
    )
    parameters = {
        "type": "object",
        "required": ["files"],
        "properties": {
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Path(s) of the finished deliverable file(s) to show.",
            },
            "note": {
                "type": "string",
                "description": "Optional one-line caption shown with the deliverables.",
            },
        },
    }

    def __init__(self, config):
        self.config = config

    async def execute(self, tool_call_id, params, abort, on_update=None):
        raw = params.get("files") or []
        if isinstance(raw, str):
            raw = [raw]
        ws = current_workspace(str(getattr(self.config, "workspace", "."))) or "."
        shown: list[str] = []
        missing: list[str] = []
        for p in raw:
            ap = Path(p) if Path(p).is_absolute() else Path(ws) / p
            (shown if ap.is_file() else missing).append(str(ap))
        note = (params.get("note") or "").strip()
        if not shown:
            miss = f" (none found: {', '.join(missing)})" if missing else ""
            return ToolResult.text(f"show_files: nothing to show{miss}", is_error=True)
        lines = [note] if note else []
        lines.append("Showing: " + ", ".join(Path(p).name for p in shown))
        if missing:
            lines.append("skipped (not found): " + ", ".join(Path(m).name for m in missing))
        # the ToolResult.artifacts channel is what actually renders them
        return ToolResult.text("\n".join(lines), artifacts=shown)

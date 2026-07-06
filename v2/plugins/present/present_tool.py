"""present_files — the universal "here are my deliverables" tool.

Producing tools (figures, video, slides, diagrams, tts, …) already declare their own
outputs. This is the GENERIC escape hatch: any agent that creates a file some other way
(e.g. `main` writing a chart via code) calls present_files to show it. It is the only
generic path by which a file becomes a rendered deliverable — nothing is inferred from
text, so a file merely found / read / listed is never shown. Decoupled: the tool just
resolves the paths and hands them to the deliverable channel (ToolResult.artifacts).
"""

from __future__ import annotations

from pathlib import Path

from agentd.application.interfaces.tool import Tool, ToolResult
from agentd.application.run_context import current_workspace


class PresentFilesTool(Tool):
    name = "present_files"
    label = "Present Files"
    description = (
        "Present the finished deliverable FILE(S) you produced so the user sees them — "
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
                "description": "Path(s) of the finished deliverable file(s) to present.",
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
        present: list[str] = []
        missing: list[str] = []
        for p in raw:
            ap = Path(p) if Path(p).is_absolute() else Path(ws) / p
            (present if ap.is_file() else missing).append(str(ap))
        note = (params.get("note") or "").strip()
        if not present:
            miss = f" (none found: {', '.join(missing)})" if missing else ""
            return ToolResult.text(f"present_files: nothing to present{miss}", is_error=True)
        lines = [note] if note else []
        lines.append("Presented: " + ", ".join(Path(p).name for p in present))
        if missing:
            lines.append("skipped (not found): " + ", ".join(Path(m).name for m in missing))
        # the ToolResult.artifacts channel is what actually renders them
        return ToolResult.text("\n".join(lines), artifacts=present)

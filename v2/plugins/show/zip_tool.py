"""zip_files — bundle produced files into a single .zip deliverable to download.

The agent-prepared bundle: pass the finished files and an out_path; the tool zips them and
declares the .zip via the deliverable channel (ToolResult.artifacts), so it shows up as a
downloadable card. Only bundle real outputs — files merely read/searched/listed have no
business here (and, like every tool, an undeclared file can never surface).
"""

from __future__ import annotations

import asyncio
import zipfile
from pathlib import Path

from agentd.application.interfaces.tool import Tool, ToolResult
from agentd.application.run_context import current_workspace


def _human(n: float) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{int(n)} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"


class ZipFilesTool(Tool):
    name = "zip_files"
    label = "Zip Files"
    description = (
        "Bundle the finished deliverable file(s) you produced into ONE .zip so the user can "
        "download them all at once. Pass the file paths and an out_path for the zip (relative "
        "to your workspace or absolute); it renders as a downloadable card. Only bundle real "
        "outputs you created — not files you merely read, searched, or listed."
    )
    parameters = {
        "type": "object",
        "required": ["files"],
        "properties": {
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Path(s) of the files to bundle into the zip.",
            },
            "out_path": {
                "type": "string",
                "description": "Output .zip path (default 'bundle.zip' in your workspace).",
            },
            "note": {"type": "string", "description": "Optional one-line caption."},
        },
    }

    def __init__(self, config):
        self.config = config

    def _resolve(self, p: str) -> Path:
        ws = current_workspace(str(getattr(self.config, "workspace", "."))) or "."
        path = Path(p)
        return path if path.is_absolute() else Path(ws) / path

    def _run(self, params: dict) -> dict:
        raw = params.get("files") or []
        if isinstance(raw, str):
            raw = [raw]
        srcs = [self._resolve(p) for p in raw]
        existing = [p for p in srcs if p.is_file()]
        missing = [str(p) for p in srcs if not p.is_file()]
        if not existing:
            raise FileNotFoundError(f"no files to bundle (missing: {missing})")
        out = self._resolve(params.get("out_path") or "bundle.zip")
        if out.suffix.lower() != ".zip":
            out = out.with_suffix(".zip")
        out.parent.mkdir(parents=True, exist_ok=True)
        used: dict[str, int] = {}
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for p in existing:
                name = p.name  # de-collide identical basenames from different dirs
                if name in used:
                    used[name] += 1
                    name = f"{p.stem}_{used[name]}{p.suffix}"
                else:
                    used[name] = 0
                z.write(p, arcname=name)
        return {"out_path": str(out), "count": len(existing), "size": out.stat().st_size,
                "missing": missing}

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            r = await asyncio.to_thread(self._run, params)
        except Exception as e:
            return ToolResult.text(f"zip_files failed: {e}", is_error=True)
        note = (params.get("note") or "").strip()
        lines = [note] if note else []
        lines.append(f"Bundled {r['count']} file(s) -> {r['out_path']} ({_human(r['size'])}).")
        if r["missing"]:
            lines.append("skipped (not found): " + ", ".join(Path(m).name for m in r["missing"]))
        return ToolResult.text("\n".join(lines), details=r, artifacts=[r["out_path"]])

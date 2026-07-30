"""plantuml: render PlantUML source (or a .puml file) to a PNG.

PlantUML is one specific diagram engine (not "any diagram", not "any image") — but it covers a
lot: sequence, class, component, state, activity, use-case, ER, gantt, mindmap, JSON/YAML, etc.

Bakes in the gotcha we learned the hard way: PlantUML's default 4096px cap CROPS large diagrams
(keeps the top-left, cuts off right/bottom boxes). This tool always passes -DPLANTUML_LIMIT_SIZE
so diagrams render whole. It returns the PNG path AND pixel dimensions, because zoom/focus boxes
are fractions of those dimensions.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_workspace


def _ffprobe_dims(path: Path):
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "csv=s=x:p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
        ).stdout.strip()
        w, h = out.split("x")
        return int(w), int(h)
    except Exception:
        return None, None


def _resolve_jar(param_jar: str | None, config) -> str:
    for cand in (
        param_jar,
        os.environ.get("PLANTUML_JAR"),
        getattr(config, "plantuml_jar", None),
        "plantuml.jar",
    ):
        if cand and (Path(cand).is_file() or cand == "plantuml.jar"):
            return str(cand)
    return "plantuml.jar"


class PlantumlTool(Tool):
    name = "plantuml"
    description = (
        "Render a PlantUML diagram to a PNG. PlantUML covers many diagram types (sequence, class, "
        "component, state, activity, use-case, ER, gantt, mindmap, JSON, ...). Give `source` "
        "(PlantUML text) or `puml_path`. Always renders WHOLE (raises PlantUML's 4096px cap so boxes "
        "aren't clipped). Returns the PNG path and its pixel width/height — zoom/focus boxes are "
        "fractions of these. For non-Latin labels, pass `font` (e.g. 'Yu Gothic UI')."
    )
    label = "PlantUML"
    concurrency = "parallel"
    parameters = {
        "type": "object",
        "required": ["out_path"],
        "properties": {
            "source": {
                "type": "string",
                "description": "PlantUML source text (@startuml ... @enduml). Provide this OR puml_path.",
            },
            "puml_path": {
                "type": "string",
                "description": "Path to a .puml file. Provide this OR source.",
            },
            "out_path": {
                "type": "string",
                "description": "Desired output PNG path (e.g. diagrams/hires/x.png), absolute or relative to workspace.",
            },
            "format": {
                "type": "string",
                "enum": ["png", "svg"],
                "description": "Output format. Default png.",
            },
            "dpi": {
                "type": "integer",
                "minimum": 60,
                "maximum": 600,
                "description": "Raster DPI for png. Default 200.",
            },
            "size_limit": {
                "type": "integer",
                "description": "PLANTUML_LIMIT_SIZE (max px before clipping). Default 16384.",
            },
            "charset": {"type": "string", "description": "Source charset. Default UTF-8."},
            "font": {
                "type": "string",
                "description": "Inject skinparam defaultFontName (only when using `source`); use a CJK face for Japanese/Chinese/Korean labels.",
            },
            "jar": {
                "type": "string",
                "description": "Path to plantuml.jar. Defaults to $PLANTUML_JAR or plantuml.jar on PATH.",
            },
        },
    }

    def __init__(self, config):
        self.config = config

    def _resolve(self, p: str) -> Path:
        path = Path(p)
        if path.is_absolute():
            return path
        ws = current_workspace(str(getattr(self.config, "workspace", "."))) or "."
        return Path(ws) / path

    def _run(self, params: dict) -> dict:
        if not params.get("source") and not params.get("puml_path"):
            raise ValueError("provide either `source` or `puml_path`")
        fmt = params.get("format", "png")
        dpi = int(params.get("dpi", 200))
        limit = int(params.get("size_limit", 16384))
        charset = params.get("charset", "UTF-8")
        jar = _resolve_jar(params.get("jar"), self.config)
        out = self._resolve(params["out_path"])
        out.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            if params.get("puml_path"):
                puml = self._resolve(params["puml_path"])
                if not puml.is_file():
                    raise FileNotFoundError(f"puml_path not found: {puml}")
            else:
                src = params["source"]
                if params.get("font"):
                    # inject the font right after the first @startuml line
                    lines = src.splitlines()
                    for i, ln in enumerate(lines):
                        if ln.strip().lower().startswith("@startuml"):
                            lines.insert(i + 1, f'skinparam defaultFontName "{params["font"]}"')
                            break
                    src = "\n".join(lines)
                puml = tdp / (out.stem + ".puml")
                puml.write_text(src, encoding="utf-8")

            cmd = [
                "java",
                f"-DPLANTUML_LIMIT_SIZE={limit}",
                "-jar",
                jar,
                "-charset",
                charset,
                f"-t{fmt}",
                f"-Sdpi={dpi}",
                str(puml),
                "-o",
                str(tdp),
            ]
            p = subprocess.run(cmd, capture_output=True, text=True)
            produced = list(tdp.glob(f"*.{fmt}"))
            if p.returncode != 0 or not produced:
                raise RuntimeError((p.stderr or p.stdout or "plantuml failed").strip()[:2000])
            shutil.move(str(produced[0]), str(out))

        w, h = _ffprobe_dims(out) if fmt == "png" else (None, None)
        return {"path": str(out), "width": w, "height": h, "format": fmt}

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            r = await asyncio.to_thread(self._run, params)
        except Exception as e:
            return ToolResult.text(f"plantuml failed: {e}", is_error=True)
        dims = f"{r['width']}x{r['height']}" if r.get("width") else r["format"]
        return ToolResult.text(
            f"Rendered PlantUML -> {r['path']} ({dims}).", details=r, artifacts=[r["path"]]
        )  # deliverable: the diagram

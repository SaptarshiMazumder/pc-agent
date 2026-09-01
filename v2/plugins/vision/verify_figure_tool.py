"""verify_figure: VLM judge — does this figure actually contain exactly the expected structures,
correctly placed and labelled?

The correctness gate for the figure loop. Raw generation is plausible-but-not-guaranteed; this asks
a vision model to compare the rendered figure against the EXPECTED structures/relationships and
report what's missing, extra, or wrong. The agent runs this before export and fixes issues. It is a
structural/visual check, NOT domain fact-checking — treat a PASS as "looks right", not "is true".
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_workspace
from agent_runtime.application.tool_models import resolve_tool_model

import vision_gemini as vg

_PROMPT = """You are a meticulous scientific-figure reviewer. Compare the image to this spec.

EXPECTED:
{expected}

Check, strictly and visually:
- Are all expected structures present? (list any MISSING)
- Are there extra/invented structures that shouldn't be there? (list EXTRA)
- Are labels spelled correctly and pointing at the right structure? (list WRONG)
- Are relationships/arrows/flow directions as described? (list issues)

Return ONLY JSON:
{{"ok": true|false,
  "missing": [..], "extra": [..], "wrong": [..], "notes": "<short overall judgement>"}}
Set ok=false if anything material is missing or wrong."""


class VerifyFigureTool(Tool):
    name = "verify_figure"
    plugin = "vision"
    needs_model = True
    model_kind = "vision"  # LOOKS AT a figure — needs a multimodal (vision) model
    default_model = vg.DEFAULT_MODEL   # cheap judge; config plugins.vision.* overrides
    description = (
        "VLM correctness check on a figure: does the image contain EXACTLY the expected structures, "
        "correctly placed/labelled, with the right relationships? Input: `image` (path) and "
        "`expected` (the structures/relationships to check, as text or a list). Returns "
        "{ok, missing, extra, wrong, notes}. Run before export and fix anything flagged. Structural/"
        "visual check only — a PASS means 'looks right', not 'scientifically true'. Uses Gemini "
        "(GEMINI_API_KEY/GOOGLE_API_KEY)."
    )
    label = "Verify Figure"
    concurrency = "parallel"
    parameters = {
        "type": "object",
        "required": ["image", "expected"],
        "properties": {
            "image": {"type": "string", "description": "Path to the figure image to check."},
            "expected": {"description": "What the figure SHOULD contain — a description string or a list of items.",
                         "type": ["string", "array"], "items": {"type": "string"}},
            "model": {"type": "string", "description": f"Override the judge model (litellm 'provider/model'; bare id => gemini). Resolves per-call > agent.toml/config plugins.vision.tools.verify_figure > plugins.vision default > {vg.DEFAULT_MODEL}."},
            "api_key": {"type": "string", "description": "Override key (else GEMINI_API_KEY/GOOGLE_API_KEY)."},
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
        img = self._resolve(params["image"])
        expected = params["expected"]
        if isinstance(expected, list):
            expected = "\n".join(f"- {x}" for x in expected)
        model = resolve_tool_model(self.config, self.plugin, self.name,
                                   per_call=params.get("model"), default=vg.DEFAULT_MODEL)
        raw = vg.analyze(img, _PROMPT.format(expected=expected),
                         model=model, api_key=params.get("api_key"), want_json=True)
        verdict = vg.parse_json(raw)
        return verdict if isinstance(verdict, dict) else {"ok": False, "notes": str(verdict)}

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            r = await asyncio.to_thread(self._run, params)
        except Exception as e:
            return ToolResult.text(f"verify_figure failed: {e}", is_error=True)
        if r.get("ok"):
            return ToolResult.text(f"PASS — {r.get('notes', 'figure matches the expected structures.')}",
                                   details=r)
        issues = []
        for k in ("missing", "extra", "wrong"):
            if r.get(k):
                issues.append(f"{k}: {', '.join(map(str, r[k]))}")
        return ToolResult.text(
            "NEEDS WORK — " + (r.get("notes", "") + " | " + " ; ".join(issues)).strip(" |"),
            details=r)

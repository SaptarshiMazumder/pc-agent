"""tts: synthesize speech from text with edge-tts.

A thin, single-purpose tool — text in, an audio file out (plus its measured duration,
which callers need to time visuals to the narration). Runs edge-tts with the SAME python
interpreter that runs agentd, so it uses the project venv (the system python's aiodns fails
in some environments).
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_workspace


def _ffprobe_duration(path: Path) -> float | None:
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nokey=1:noprint_wrappers=1",
                str(path),
            ],
            capture_output=True,
            text=True,
        ).stdout.strip()
        return round(float(out), 3)
    except Exception:
        return None


class TtsTool(Tool):
    name = "tts"
    description = (
        "Synthesize speech from text into an audio file (edge-tts). Returns the file path and its "
        "duration in seconds (use the duration to time slides/diagram zooms to the narration). "
        "Pick a `voice` that fits the language and speaker; call once per speaker/segment."
    )
    label = "TTS"
    concurrency = "parallel"
    parameters = {
        "type": "object",
        "required": ["text", "out_path"],
        "properties": {
            "text": {"type": "string", "description": "The text to speak."},
            "out_path": {
                "type": "string",
                "description": "Output audio file (e.g. .mp3), absolute or relative to the workspace.",
            },
            "voice": {
                "type": "string",
                "description": "edge-tts voice, e.g. en-US-AndrewMultilingualNeural, ja-JP-KeitaNeural. Default en-US-AndrewMultilingualNeural.",
            },
            "rate": {
                "type": "string",
                "description": "Speaking rate like +0%, +12%, -10%. Default +0%.",
            },
            "volume": {"type": "string", "description": "Volume like +0%, -20%. Default +0%."},
            "pitch": {"type": "string", "description": "Pitch like +0Hz, -10Hz. Default +0Hz."},
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

    @staticmethod
    def _norm(val: str, unit: str) -> str:
        val = (val or "").strip() or f"+0{unit}"
        if val[0] not in "+-":
            val = "+" + val
        return val

    def _run(self, params: dict) -> dict:
        text = params["text"]
        voice = params.get("voice") or "en-US-AndrewMultilingualNeural"
        rate = self._norm(params.get("rate", "+0%"), "%")
        volume = self._norm(params.get("volume", "+0%"), "%")
        pitch = self._norm(params.get("pitch", "+0Hz"), "Hz")
        out = self._resolve(params["out_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        txt = out.with_suffix(".tts.txt")
        txt.write_text(text, encoding="utf-8")
        try:
            p = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "edge_tts",
                    "--voice",
                    voice,
                    "--rate",
                    rate,
                    "--volume",
                    volume,
                    "--pitch",
                    pitch,
                    "-f",
                    str(txt),
                    "--write-media",
                    str(out),
                ],
                capture_output=True,
                text=True,
            )
        finally:
            txt.unlink(missing_ok=True)
        if p.returncode != 0 or not out.exists():
            raise RuntimeError((p.stderr or p.stdout or "edge-tts failed").strip())
        return {"path": str(out), "duration_sec": _ffprobe_duration(out), "voice": voice}

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            r = await asyncio.to_thread(self._run, params)
        except Exception as e:
            return ToolResult.text(f"tts failed: {e}", is_error=True)
        dur = f"{r['duration_sec']:.2f}s" if r["duration_sec"] is not None else "unknown duration"
        return ToolResult.text(
            f"Wrote speech to {r['path']} ({dur}, voice {r['voice']}).",
            details=r,
            artifacts=[r["path"]],
        )  # deliverable: the audio

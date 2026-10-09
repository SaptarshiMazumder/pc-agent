"""MediaProbe with ffmpeg (imageio-ffmpeg's static binary): its stream header for the length and the
frame rate, and a full decode to nowhere for whether the file plays."""

from __future__ import annotations

import re
import subprocess

from ad_generation.infrastructure.run_workspace import RunWorkspace


class FfmpegMediaProbe:
    def __init__(self, workspace: RunWorkspace, ffmpeg: str) -> None:
        self._ws = workspace
        self._ffmpeg = ffmpeg

    def seconds(self, path: str) -> float:
        m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", self._header(path))
        if not m:
            raise ValueError(f"cannot read how long {path} runs")
        return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])

    def frame_rate(self, path: str) -> float:
        m = re.search(r"Video:.*?(\d+(?:\.\d+)?) fps", self._header(path))
        if not m:
            raise ValueError(f"cannot read the frame rate of {path}")
        return float(m[1])

    def decode_errors(self, path: str) -> str:
        res = subprocess.run(
            [self._ffmpeg, "-hide_banner", "-v", "error", "-i", str(self._ws.path(path)), "-f", "null", "-"],
            capture_output=True, text=True,
        )
        return (res.stderr.strip() or (f"ffmpeg exited {res.returncode}" if res.returncode else ""))[:600]

    def _header(self, path: str) -> str:
        return subprocess.run([self._ffmpeg, "-hide_banner", "-i", str(self._ws.path(path))], capture_output=True, text=True).stderr

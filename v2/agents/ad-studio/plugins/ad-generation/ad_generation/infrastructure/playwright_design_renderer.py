"""DesignRenderer with a headless Chromium (Playwright) and ffmpeg.

A design is opened as a page at the format's exact size, with the workspace as its base — so a
picture is named by its workspace path — and locked down: it may load files from the workspace and
fonts from Google Fonts, nothing else. Its CSS animations are paused and stepped frame by frame,
so a capture is exact and repeatable.

A still is one screenshot (taken 1.5 s in, when entrance animations have played and exits have not
begun). Motion is frames: without a clip, the whole page per frame; with a clip, the page minus its
`data-over` elements once (the ground), the clip scaled into its `data-video` slot, and the
`data-over` elements per frame on a transparent background — composited by ffmpeg.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

from ad_generation.application.interfaces.media_probe import MediaProbe
from ad_generation.domain.instagram_format import FPS, size
from ad_generation.domain.post import ClipEdit
from ad_generation.infrastructure.run_workspace import RunWorkspace

_CAPTURE_FPS = 24
_STILL_AT_MS = 1500
_FONT_HOSTS = ("fonts.googleapis.com", "fonts.gstatic.com")
_STEP = """ms => document.getAnimations().forEach(a => { a.pause(); a.currentTime = ms; })"""
_OVERLAY_ONLY = (
    "html, body { background: transparent !important; } body * { visibility: hidden !important; } "
    "[data-over], [data-over] * { visibility: visible !important; }"
)
_GROUND_ONLY = "[data-over] { visibility: hidden !important; }"


class PlaywrightDesignRenderer:
    def __init__(self, workspace: RunWorkspace, ffmpeg: str, probe: MediaProbe) -> None:
        self._ws = workspace
        self._ffmpeg = ffmpeg
        self._probe = probe

    # ---- the port ------------------------------------------------------------------------------

    def animated(self, html: str) -> bool:
        return bool(re.search(r"@keyframes|animation\s*:", self._ws.path(html).read_text(encoding="utf-8")))

    def still(self, html: str, fmt: str, out: str) -> str:
        w, h = size(fmt)
        with self._page(w, h) as page:
            self._open(page, html)
            self._poster(page, out)
            page.evaluate(_STEP, _STILL_AT_MS)
            self._shot(page, out, transparent=False)
        return out

    def motion(self, html: str, fmt: str, out: str, seconds: float, clip: str = "", edit: ClipEdit | None = None) -> str:
        w, h = size(fmt)
        edit = edit or ClipEdit()
        work = self._ws.path(out).with_suffix(".frames")
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True)
        try:
            with self._page(w, h) as page:
                self._open(page, html)
                if clip:
                    run = self._seconds(clip) - edit.trim_start - edit.trim_end
                    if run <= 0.2:
                        raise ValueError(f"{clip}: the trims leave nothing of the clip")
                    length = run / edit.speed
                    slot = page.evaluate(
                        "() => { const e = document.querySelector('[data-video]'); if (!e) return null;"
                        " const r = e.getBoundingClientRect(); return [r.x, r.y, r.width, r.height]; }"
                    )
                    if not slot:
                        raise ValueError(f"{html}: a design for a clip needs one element with data-video — the slot the clip plays in")
                    page.add_style_tag(content=_GROUND_ONLY)
                    page.evaluate(_STEP, 0)
                    self._shot(page, str(work / "ground.png"), transparent=False, absolute=True)
                    page.add_style_tag(content=_OVERLAY_ONLY)
                    frames = self._frames(page, work, length, transparent=True)
                    # A carousel clip keeps its own frame rate; a Reel's parts share the format's.
                    rate = self._probe.frame_rate(clip) if fmt == "carousel" else FPS
                    self._compose_clip(work, frames, clip, edit, slot, length, out, rate)
                else:
                    length = seconds
                    self._poster(page, out)
                    frames = self._frames(page, work, length, transparent=False)
                    self._encode_frames(work, frames, length, out)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        return out

    # ---- the page ------------------------------------------------------------------------------

    @contextmanager
    def _page(self, w: int, h: int):
        root = self._ws.path(".").resolve().as_uri().rstrip("/") + "/"
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": w, "height": h}, device_scale_factor=1)

                def gate(route):
                    url = route.request.url
                    host = urlsplit(url).hostname or ""
                    if url.startswith(root) or url.startswith("data:") or host in _FONT_HOSTS:
                        route.continue_()
                    else:
                        route.abort()  # a design reaches only the workspace's files and fonts

                page.route("**/*", gate)
                yield page
            finally:
                browser.close()

    def _open(self, page, html: str) -> None:
        text = self._ws.path(html).read_text(encoding="utf-8")
        base = f'<base href="{self._ws.path(".").resolve().as_uri().rstrip("/")}/">'
        text = re.sub(r"<head([^>]*)>", lambda m: f"<head{m.group(1)}>{base}", text, count=1) if "<head" in text else base + text
        page_file = self._ws.path(html).with_suffix(".page.html")
        page_file.write_text(text, encoding="utf-8")
        try:
            page.goto(page_file.resolve().as_uri(), wait_until="networkidle")
            page.evaluate("() => document.fonts.ready.then(() => true)")
            page.evaluate("() => Promise.all([...document.images].map(i => i.complete ? 1 : new Promise(r => { i.onload = i.onerror = r; })))")
        finally:
            page_file.unlink(missing_ok=True)

    def _poster(self, page, out: str) -> None:
        """A clip's slot, where no clip plays, shows the clip's middle frame."""
        clip = page.evaluate("() => { const e = document.querySelector('[data-video]'); return e ? e.getAttribute('data-video') : ''; }")
        if not clip:
            return
        frame = self._ws.path(out).with_suffix(".poster.jpg")
        self._run(["-ss", f"{self._seconds(clip) / 2:.3f}", "-i", str(self._ws.path(clip)), "-frames:v", "1", "-q:v", "3", str(frame)])
        page.evaluate(
            "src => { const e = document.querySelector('[data-video]'); e.style.backgroundImage = `url('${src}')`;"
            " e.style.backgroundSize = 'cover'; e.style.backgroundPosition = 'center'; }",
            frame.resolve().as_uri(),
        )
        page.wait_for_timeout(150)

    def _frames(self, page, work: Path, length: float, transparent: bool) -> int:
        count = max(1, int(round(length * _CAPTURE_FPS)))
        if not self._moves(page):
            page.evaluate(_STEP, 0)
            self._shot(page, str(work / "f00000.png"), transparent=transparent, absolute=True)
            return 1
        for n in range(count):
            page.evaluate(_STEP, n * 1000 / _CAPTURE_FPS)
            self._shot(page, str(work / f"f{n:05d}.png"), transparent=transparent, absolute=True)
        return count

    @staticmethod
    def _moves(page) -> bool:
        return bool(page.evaluate("() => document.getAnimations().length"))

    def _shot(self, page, out: str, transparent: bool, absolute: bool = False) -> None:
        path = out if absolute else str(self._ws.path(out))
        if path.lower().endswith((".jpg", ".jpeg")):
            page.screenshot(path=path, type="jpeg", quality=93)
        else:
            page.screenshot(path=path, omit_background=transparent)

    # ---- ffmpeg --------------------------------------------------------------------------------

    def _compose_clip(self, work: Path, frames: int, clip: str, edit: ClipEdit, slot, length: float, out: str, rate: float) -> None:
        x, y, bw, bh = (int(round(v)) for v in slot)
        bw, bh = max(2, bw // 2 * 2), max(2, bh // 2 * 2)
        overlay = (["-loop", "1", "-t", f"{length:.3f}", "-i", str(work / "f00000.png")] if frames == 1
                   else ["-framerate", str(_CAPTURE_FPS), "-i", str(work / "f%05d.png")])
        graph = (
            f"[0:v]fps={rate},format=rgba[g];"
            f"[1:v]setpts=(PTS-STARTPTS)/{edit.speed},scale={bw}:{bh}:force_original_aspect_ratio=increase,"
            f"crop={bw}:{bh},setsar=1,fps={rate}[c];[g][c]overlay=x={x}:y={y}:shortest=1[v];"
            f"[2:v]fps={rate},format=rgba[o];[v][o]overlay=0:0:eof_action=repeat"
            + self._fades(edit.fade_in, edit.fade_out, length) + ",format=yuv420p[out]"
        )
        run = self._seconds(clip) - edit.trim_start - edit.trim_end
        self._run(
            ["-loop", "1", "-t", f"{length:.3f}", "-i", str(work / "ground.png"),
             "-ss", f"{edit.trim_start:.3f}", "-t", f"{run:.3f}", "-i", str(self._ws.path(clip)), *overlay,
             "-filter_complex", graph, "-map", "[out]", "-t", f"{length:.3f}", "-an", "-r", str(rate),
             "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
             str(self._ws.path(out))]
        )

    def _encode_frames(self, work: Path, frames: int, length: float, out: str) -> None:
        source = (["-loop", "1", "-t", f"{length:.3f}", "-i", str(work / "f00000.png")] if frames == 1
                  else ["-framerate", str(_CAPTURE_FPS), "-i", str(work / "f%05d.png")])
        self._run(
            [*source, "-vf", f"fps={FPS},format=yuv420p", "-t", f"{length:.3f}", "-an", "-r", str(FPS),
             "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
             str(self._ws.path(out))]
        )

    @staticmethod
    def _fades(fade_in: float, fade_out: float, length: float) -> str:
        parts = []
        if fade_in > 0:
            parts.append(f"fade=t=in:st=0:d={fade_in:.3f}")
        if fade_out > 0:
            parts.append(f"fade=t=out:st={max(length - fade_out, 0):.3f}:d={fade_out:.3f}")
        return "".join("," + p for p in parts)

    def _seconds(self, clip: str) -> float:
        return self._probe.seconds(clip)

    def _run(self, args: list[str]) -> None:
        res = subprocess.run([self._ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args], capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {res.stderr.strip()[-600:]}")

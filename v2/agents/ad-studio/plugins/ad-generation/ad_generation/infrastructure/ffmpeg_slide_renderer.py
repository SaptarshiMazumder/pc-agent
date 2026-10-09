"""SlideRenderer with Pillow and ffmpeg (imageio-ffmpeg's static binary).

A plain slide (one without a design — designs have their own engine) is the photo or clip
filling the frame (scaled to cover it, centre-cropped) with its words over it: each text cue drawn
ONCE by Pillow in the brand's font onto a transparent layer, wrapped to the safe width, with soft
shadows so it reads over any picture.
A still whose words do not move is those layers flattened (.jpg). Anything that moves is ffmpeg:
the clip sped up and trimmed, each text layer faded in and out WHILE IT PLAYS and slid when the cue
moves, the slide faded from and to black at its ends (.mp4, H.264, no audio — the music is added in
Instagram). A carousel clip keeps its own frame rate (re-timing 24 to 30 judders); a still that moves,
and every part of a Reel, run at the format's rate.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ad_generation.domain.brand_profile import BrandProfile
from ad_generation.domain.instagram_format import FPS, size
from ad_generation.application.interfaces.media_probe import MediaProbe
from ad_generation.domain.post import Slide, TextCue
from ad_generation.infrastructure.run_workspace import RunWorkspace

_LINE = 1.18
_MIN_PX = 14
_AREA = (0.07, 0.06, 0.86, 0.88)  # where words may sit: x, y, w, h fractions


class FfmpegSlideRenderer:
    def __init__(self, workspace: RunWorkspace, fonts: Path, ffmpeg: str, probe: MediaProbe) -> None:
        self._ws = workspace
        self._fonts = fonts
        self._ffmpeg = ffmpeg
        self._probe = probe

    # ---- the slides ----------------------------------------------------------------------------

    def render(self, slide: Slide, brand: BrandProfile, fmt: str, out_stem: str, as_video: bool) -> str:
        w, h = size(fmt)
        area = self._px(_AREA, w, h)
        layers = [
            self._cue_layer(c, brand, w, h, f"{out_stem}.cue{n}.png", area, brand.text_color, shadow=True)
            for n, c in enumerate(slide.cues)
        ]
        temp = list(layers)
        try:
            if slide.kind == "image":
                with Image.open(self._ws.path(slide.item)) as im:
                    photo = im.convert("RGB")
                base = self._cover(photo, w, h).convert("RGBA")
                if not (as_video or slide.as_video):
                    for layer in layers:
                        with Image.open(self._ws.path(layer)) as text:
                            base.alpha_composite(text)
                    out = f"{out_stem}.jpg"
                    base.convert("RGB").save(self._ws.path(out), "JPEG", quality=95)
                    return out
                base_png = f"{out_stem}.base.png"
                base.save(self._ws.path(base_png), "PNG")
                temp.append(base_png)
                out = f"{out_stem}.mp4"
                self._encode(
                    ["-loop", "1", "-t", f"{slide.seconds:.3f}", "-i", str(self._ws.path(base_png))],
                    "[0:v]fps=30,format=rgba[v0]", 1, slide, layers, h, slide.seconds, 0.0, 0.0, out,
                )
                return out
            out = f"{out_stem}.mp4"
            # A carousel clip keeps its own frame rate; a Reel's parts share the format's.
            self._clip(slide, w, h, layers, out, self._probe.frame_rate(slide.item) if fmt == "carousel" else FPS)
            return out
        finally:
            self._drop(temp)

    def stitch(self, parts: list[str], out: str) -> str:
        listing = self._ws.path(out + ".txt")
        listing.write_text("".join(f"file '{self._ws.path(p).as_posix()}'\n" for p in parts), encoding="utf-8")
        self._run(["-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", "-movflags", "+faststart", str(self._ws.path(out))])
        listing.unlink()
        return out

    def still_of(self, clip: str, out: str) -> str:
        self._run(["-ss", f"{self.seconds(clip) / 2:.3f}", "-i", str(self._ws.path(clip)), "-frames:v", "1", "-q:v", "3", str(self._ws.path(out))])
        return out

    def seconds(self, clip: str) -> float:
        return self._probe.seconds(clip)

    # ---- video ---------------------------------------------------------------------------------

    def _clip(self, slide, w, h, layers, out, rate: float) -> None:
        """The clip filling the frame — sped up and trimmed — with its words over it, at `rate` fps."""
        e = slide.edit
        run = self.seconds(slide.item) - e.trim_start - e.trim_end
        if run <= 0.2:
            raise ValueError(f"{slide.item}: the trims leave nothing of the clip")
        length = run / e.speed
        args = ["-ss", f"{e.trim_start:.3f}", "-t", f"{run:.3f}", "-i", str(self._ws.path(slide.item))]
        graph = (
            f"[0:v]setpts=(PTS-STARTPTS)/{e.speed},scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},setsar=1,fps={rate},format=rgba[v0]"
        )
        self._encode(args, graph, 1, slide, layers, h, length, e.fade_in, e.fade_out, out, rate)

    def _encode(self, args, base_graph, first_input, slide, layers, h, length, fade_in, fade_out, out, rate: float = FPS) -> None:
        """`base_graph` ends in [v0]; each text layer then fades and moves over it."""
        graph = [base_graph]
        for n, (cue, layer) in enumerate(zip(slide.cues, layers), 1):
            idx = first_input + n - 1
            args = args + ["-loop", "1", "-t", f"{length:.3f}", "-i", str(self._ws.path(layer))]
            graph.append(f"[{idx}:v]format=rgba{self._fades(cue, length)}[t{n}]")
            y = "0"
            if cue.moves:
                shift = (cue.y - cue.to_y) * h
                y = f"'-{shift:.1f}*min(max((t-{cue.move_at:.3f})/{max(cue.move_s, 0.05):.3f},0),1)'"
            graph.append(f"[v{n - 1}][t{n}]overlay=x=0:y={y}:eval=frame[v{n}]")
        tail = []
        if fade_in > 0:
            tail.append(f"fade=t=in:st=0:d={fade_in:.3f}")
        if fade_out > 0:
            tail.append(f"fade=t=out:st={max(length - fade_out, 0):.3f}:d={fade_out:.3f}")
        graph.append(f"[v{len(layers)}]{','.join(tail + ['format=yuv420p'])}[out]")
        self._run(
            args
            + ["-filter_complex", ";".join(graph), "-map", "[out]", "-t", f"{length:.3f}", "-an", "-r", str(rate),
               "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
               str(self._ws.path(out))]
        )

    @staticmethod
    def _fades(cue: TextCue, length: float) -> str:
        """The text layer's alpha over time: in from `start`, out by `end` (0 = it stays)."""
        parts = []
        if cue.start > 0 or cue.fade_in > 0:
            parts.append(f"fade=t=in:st={cue.start:.3f}:d={max(cue.fade_in, 0.01):.3f}:alpha=1")
        if cue.end:
            out_at = max(min(cue.end, length) - cue.fade_out, cue.start)
            parts.append(f"fade=t=out:st={out_at:.3f}:d={max(cue.fade_out, 0.01):.3f}:alpha=1")
        return "".join("," + p for p in parts)

    # ---- text ----------------------------------------------------------------------------------

    def _cue_layer(self, cue: TextCue, brand: BrandProfile, w: int, h: int, out: str, area, ink: str, shadow: bool) -> str:
        """The cue's words on a transparent full frame: wrapped to the text area's width, centred on
        its y but kept inside the area."""
        ax, ay, aw, ah = area
        font_path = str(self._fonts / brand.font_file(cue.face))
        px = int(cue.size * h)
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        while True:
            font = ImageFont.truetype(font_path, px)
            lines = self._balanced(draw, cue.text, font, aw)
            widest = max(draw.textlength(line, font=font) for line in lines)
            block = px * _LINE * len(lines)
            if (widest <= aw and block <= ah) or px <= _MIN_PX:
                break
            px -= max(1, px // 20)
        top = min(max(cue.y * h - block / 2, ay), ay + ah - block)
        color = cue.color or ink
        tight = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        wide = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        tdraw, wdraw = ImageDraw.Draw(tight), ImageDraw.Draw(wide)
        for n, line in enumerate(lines):
            lw = draw.textlength(line, font=font)
            x = ax + (aw - lw) / 2 if cue.align == "center" else (ax if cue.align == "left" else ax + aw - lw)
            y = top + n * px * _LINE
            if shadow:
                # TWO SHADOWS, no panel: a tight dark one that edges every letter, and a wide soft one
                # that quiets a busy or light picture behind the words.
                tdraw.text((x, y + px * 0.03), line, font=font, fill=(0, 0, 0, 200), stroke_width=max(1, px // 28), stroke_fill=(0, 0, 0, 200))
                wdraw.text((x, y), line, font=font, fill=(0, 0, 0, 150), stroke_width=max(2, px // 6), stroke_fill=(0, 0, 0, 150))
            draw.text((x, y), line, font=font, fill=color)
        out_img = wide.filter(ImageFilter.GaussianBlur(max(6, px // 2.5)))
        out_img.alpha_composite(tight.filter(ImageFilter.GaussianBlur(max(1, px // 18))))
        out_img.alpha_composite(layer)
        out_img.save(self._ws.path(out), "PNG")
        return out

    @classmethod
    def _balanced(cls, draw: ImageDraw.ImageDraw, text: str, font, max_w: float) -> list[str]:
        """As few lines as fit, then as even as they can be — the narrowest width that still needs
        no more lines — so a heading never leaves one word alone on its last line."""
        lines = cls._wrap(draw, text, font, max_w)
        lo, hi = 0.0, max_w
        for _ in range(14):
            mid = (lo + hi) / 2
            if len(cls._wrap(draw, text, font, mid)) <= len(lines):
                hi = mid
            else:
                lo = mid
        return cls._wrap(draw, text, font, hi)

    @staticmethod
    def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: float) -> list[str]:
        lines: list[str] = []
        for para in text.split("\n"):
            line = ""
            for word in para.split():
                trial = f"{line} {word}".strip()
                if line and draw.textlength(trial, font=font) > max_w:
                    lines.append(line)
                    line = word
                else:
                    line = trial
            lines.append(line)
        return lines

    # ---- helpers -------------------------------------------------------------------------------

    @staticmethod
    def _px(box: tuple[float, float, float, float], w: int, h: int) -> tuple[int, int, int, int]:
        return round(box[0] * w), round(box[1] * h), max(2, round(box[2] * w)) // 2 * 2, max(2, round(box[3] * h)) // 2 * 2

    @staticmethod
    def _cover(im: Image.Image, w: int, h: int) -> Image.Image:
        scale = max(w / im.width, h / im.height)
        im = im.resize((max(w, round(im.width * scale)), max(h, round(im.height * scale))), Image.LANCZOS)
        left, top = (im.width - w) // 2, (im.height - h) // 2
        return im.crop((left, top, left + w, top + h))

    def _drop(self, files: list[str]) -> None:
        for f in files:
            self._ws.path(f).unlink(missing_ok=True)

    def _run(self, args: list[str]) -> None:
        res = subprocess.run([self._ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args], capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {res.stderr.strip()[-600:]}")

"""PosterTypesetter with Pillow and the bundled fonts: each layer's words wrapped to its area at the
largest size that fits (never above the layer's size), aligned in it, centred top to bottom; a
pill is a filled rounded bar sized to its words."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ad_generation.domain.poster_text import FONTS, TextLayer
from ad_generation.infrastructure.run_workspace import RunWorkspace

_LINE = 1.15  # line height, in font sizes
_MIN_PX = 10  # smaller than this is unreadable: the text does not fit


class PillowPosterTypesetter:
    def __init__(self, workspace: RunWorkspace, fonts: Path) -> None:
        self._ws = workspace
        self._fonts = fonts

    def render(self, base: str, layers: tuple[TextLayer, ...], out: str) -> None:
        with Image.open(self._ws.path(base)) as im:
            canvas = im.convert("RGB")
        draw = ImageDraw.Draw(canvas)
        for layer in layers:
            if layer.text.strip():
                self._layer(draw, canvas.size, layer)
        canvas.save(self._ws.path(out), "PNG")

    def _font(self, key: str, px: int) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(str(self._fonts / FONTS[key]), px)

    def _layer(self, draw: ImageDraw.ImageDraw, size: tuple[int, int], layer: TextLayer) -> None:
        width, height = size
        bx, by, bw, bh = (layer.box[0] * width, layer.box[1] * height, layer.box[2] * width, layer.box[3] * height)
        pad_x = bh * 0.45 if layer.kind == "pill" else 0.0
        px = int(layer.size * height)
        while True:
            if px < _MIN_PX:
                raise ValueError(f"the {layer.role} '{layer.text}' does not fit its area — shorten it or make the area bigger")
            font = self._font(layer.font, px)
            lines = self._wrap(draw, layer.text, font, bw - 2 * pad_x)
            block_h = px * _LINE * len(lines)
            widest = max(draw.textlength(line, font=font) for line in lines)
            if lines and block_h <= bh * (0.8 if layer.kind == "pill" else 1.0) and widest <= bw - 2 * pad_x:
                break
            px -= max(1, px // 20)
        top = by + (bh - block_h) / 2
        if layer.kind == "pill":
            pill_w = widest + 2 * pad_x
            left = self._x(bx, bw, pill_w, layer.align)
            draw.rounded_rectangle((left, by, left + pill_w, by + bh), radius=bh / 2, fill=layer.fill)
        for n, line in enumerate(lines):
            w = draw.textlength(line, font=font)
            x = self._x(bx, bw, w, layer.align) if layer.kind == "text" else left + (pill_w - w) / 2
            y = top + n * px * _LINE + (px * _LINE - px) / 2
            draw.text((x, y), line, font=font, fill=layer.color, anchor="la")

    @staticmethod
    def _x(bx: float, bw: float, w: float, align: str) -> float:
        if align == "left":
            return bx
        if align == "right":
            return bx + bw - w
        return bx + (bw - w) / 2

    @staticmethod
    def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: float) -> list[str]:
        """Words onto lines no wider than max_w; the user's own line breaks are kept."""
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

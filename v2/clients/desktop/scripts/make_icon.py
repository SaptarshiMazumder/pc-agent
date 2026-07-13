"""Generate the desktop app icon from the nakama mark — GREEN ONLY, transparent.

Re-draws the two woven rings (see src/renderer/src/assets/nakama.svg) with Pillow at
4x supersampling, then exports resources/icon.png (512) and resources/icon.ico
(multi-size). Pure stdlib+Pillow so the build needs no SVG rasterizer dependency.

Run:  python scripts/make_icon.py   (from clients/desktop, any python with Pillow)
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

GREEN = (132, 204, 22, 255)          # #84CC16 — the brand lime

# Source geometry (the SVG's 256-space), tightened to the art's square bounds so
# the rings FILL an icon frame instead of floating in it.
VIEW = (40.0, 40.0, 176.0)           # x, y, side of the crop square
RING_A = (102.0, 128.0)              # left ring center
RING_B = (154.0, 128.0)              # right ring center
RADIUS = 40.0
STROKE = 12.0
HALO = 22.0                          # knockout width at the crossings
# A's corrective arc back over B at the top crossing (endpoints on circle A)
ARC_P1 = (115.7, 90.4)
ARC_P2 = (137.3, 109.2)

OUT = 512
SS = 4                               # supersampling factor


def _angle(center: tuple[float, float], p: tuple[float, float]) -> float:
    """PIL arc angle: degrees from 3 o'clock, clockwise (image y-down)."""
    return math.degrees(math.atan2(p[1] - center[1], p[0] - center[0])) % 360


def _ring(size: int, scale: float, center: tuple[float, float], width: float,
          start: float = 0, end: float = 360, caps: bool = False) -> Image.Image:
    """A stroked circle/arc as an L-mode mask (white = ink)."""
    layer = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(layer)
    cx = (center[0] - VIEW[0]) * scale
    cy = (center[1] - VIEW[1]) * scale
    outer = (RADIUS + width / 2) * scale
    draw.arc([cx - outer, cy - outer, cx + outer, cy + outer],
             start, end, fill=255, width=max(1, round(width * scale)))
    if caps:                                            # round line caps
        for p in (ARC_P1, ARC_P2):
            px = (p[0] - VIEW[0]) * scale
            py = (p[1] - VIEW[1]) * scale
            r = (width / 2) * scale
            draw.ellipse([px - r, py - r, px + r, py + r], fill=255)
    return layer


def build(size: int) -> Image.Image:
    canvas = size * SS
    scale = canvas / VIEW[2]
    a1, a2 = _angle(RING_A, ARC_P1), _angle(RING_A, ARC_P2)

    ring_a = _ring(canvas, scale, RING_A, STROKE)
    ring_b = _ring(canvas, scale, RING_B, STROKE)
    halo_b = _ring(canvas, scale, RING_B, HALO)               # cuts A at both crossings
    arc_a = _ring(canvas, scale, RING_A, STROKE, a1, a2, caps=True)   # A back over B
    halo_arc = _ring(canvas, scale, RING_A, HALO, a1, a2, caps=True)  # cuts B up top

    alpha = ImageChops.lighter(
        ImageChops.lighter(ImageChops.subtract(ring_a, halo_b),
                           ImageChops.subtract(ring_b, halo_arc)),
        arc_a)
    art = Image.new("RGBA", (canvas, canvas), GREEN[:3] + (0,))
    art.putalpha(alpha)
    return art.resize((size, size), Image.LANCZOS)


def main() -> None:
    out_dir = Path(__file__).resolve().parents[1] / "resources"
    out_dir.mkdir(exist_ok=True)
    icon = build(OUT)
    icon.save(out_dir / "icon.png")
    icon.save(out_dir / "icon.ico", format="ICO",
              sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"wrote {out_dir / 'icon.png'} and {out_dir / 'icon.ico'}")


if __name__ == "__main__":
    main()

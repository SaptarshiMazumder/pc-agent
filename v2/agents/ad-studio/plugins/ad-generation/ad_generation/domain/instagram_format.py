"""Instagram's frames: a carousel slide is 4:5 (1080x1350), a Reel 9:16 (1080x1920). A carousel
holds up to 20 slides. Text keeps clear of the edges Instagram covers (the Reel's caption and
buttons at the bottom and right)."""

from __future__ import annotations

FORMATS = ("carousel", "reel")
SIZES = {"carousel": (1080, 1350), "reel": (1080, 1920)}
MAX_SLIDES = {"carousel": 20, "reel": 30}
FPS = 30
# Where text may sit, as fractions of the height: (top, bottom) of the safe band.
SAFE = {"carousel": (0.06, 0.92), "reel": (0.12, 0.78)}


def size(fmt: str) -> tuple[int, int]:
    if fmt not in SIZES:
        raise ValueError(f"a post is a {' or a '.join(FORMATS)}, not '{fmt}'")
    return SIZES[fmt]

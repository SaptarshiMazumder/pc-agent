"""Aspect ratios: the one a model accepts that is closest to the one wanted."""

from __future__ import annotations

# What a model is assumed to take when its spec lists none.
DEFAULT = ("21:9", "16:9", "4:3", "1:1", "3:4", "9:16")


def value(ratio: str) -> float:
    w, h = ratio.split(":")
    return float(w) / float(h)


def nearest_ratio(width: float, height: float, allowed: list[str] | tuple[str, ...]) -> str:
    """The ratio, of those the model accepts, closest to width:height — keeping its orientation
    when the model offers one: a vertical picture stays vertical (a 4:5 still goes to Kling as
    9:16, not as a square)."""
    ratio = width / height
    choices = [r for r in allowed if ":" in r] or list(DEFAULT)
    same_way = [r for r in choices if (value(r) < 1) == (ratio < 1) and value(r) != 1] if ratio != 1 else []
    return min(same_way or choices, key=lambda r: abs(value(r) - ratio))

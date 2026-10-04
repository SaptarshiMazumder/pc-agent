"""Where the words go on each text-ad format, in overlay mode: fixed areas, as fractions of the
frame. The image model is told to leave exactly these areas empty (`clear_space`), and the first
layers of a design are put in them (`layers_for`); the user can move them afterwards.
"""

from __future__ import annotations

from ad_generation.domain.poster_copy import ROLES, PosterCopy
from ad_generation.domain.poster_text import DISPLAY_FONTS, TextLayer

# role -> (x, y, w, h, size, align, font: "display" = the brief's choice | "body")
_ZONES: dict[str, dict] = {
    "poster-hero": {
        "clear": "the top third of the frame across its full width, and a band along the bottom 16%",
        "zones": {
            "headline": (0.07, 0.05, 0.86, 0.17, 0.085, "center", "display"),
            "subline": (0.12, 0.225, 0.76, 0.055, 0.032, "center", "body"),
            "offer": (0.30, 0.285, 0.40, 0.045, 0.036, "center", "display"),
            "cta": (0.30, 0.855, 0.40, 0.07, 0.032, "center", "display"),
            "fine_print": (0.06, 0.945, 0.88, 0.035, 0.016, "center", "body"),
        },
    },
    "offer-sale": {
        "clear": "the top 42% of the frame across its full width, and a band along the bottom 16%",
        "zones": {
            "offer": (0.06, 0.04, 0.88, 0.22, 0.17, "center", "display"),
            "headline": (0.08, 0.27, 0.84, 0.085, 0.06, "center", "display"),
            "subline": (0.12, 0.36, 0.76, 0.05, 0.03, "center", "body"),
            "cta": (0.30, 0.855, 0.40, 0.07, 0.032, "center", "display"),
            "fine_print": (0.06, 0.945, 0.88, 0.035, 0.016, "center", "body"),
        },
    },
    "launch-announcement": {
        "clear": "the top 38% of the frame across its full width, and a band along the bottom 15%",
        "zones": {
            "headline": (0.08, 0.07, 0.84, 0.17, 0.08, "center", "display"),
            "subline": (0.12, 0.25, 0.76, 0.055, 0.03, "center", "body"),
            "offer": (0.30, 0.315, 0.40, 0.045, 0.032, "center", "display"),
            "cta": (0.32, 0.865, 0.36, 0.065, 0.03, "center", "display"),
            "fine_print": (0.06, 0.945, 0.88, 0.035, 0.016, "center", "body"),
        },
    },
    "minimal-type": {
        "clear": "the top 62% of the frame across its full width, and the bottom-left corner below 84%",
        "zones": {
            "headline": (0.07, 0.06, 0.86, 0.40, 0.14, "left", "display"),
            "subline": (0.07, 0.47, 0.66, 0.06, 0.03, "left", "body"),
            "offer": (0.07, 0.54, 0.50, 0.06, 0.035, "left", "display"),
            "cta": (0.07, 0.87, 0.34, 0.06, 0.028, "left", "display"),
            "fine_print": (0.07, 0.945, 0.86, 0.035, 0.016, "left", "body"),
        },
    },
}
DEFAULT_FORMAT = "poster-hero"


def _format(format_key: str) -> dict:
    return _ZONES.get(format_key) or _ZONES[DEFAULT_FORMAT]


def clear_space(format_key: str) -> str:
    """The areas the picture must leave empty for the words, in words for the image model."""
    return _format(format_key)["clear"]


def readable_on(fill: str) -> str:
    """Black or white, whichever reads better on `fill`."""
    r, g, b = (int(fill[i : i + 2], 16) for i in (1, 3, 5))
    return "#111111" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "#ffffff"


def layers_for(copy: PosterCopy, format_key: str, text_color: str, accent_color: str, font: str) -> tuple[TextLayer, ...]:
    """A design's first layers: each piece of copy in its format's area, in the brief's colours and
    display font; the call to action on a pill of the accent colour."""
    if font not in DISPLAY_FONTS:
        raise ValueError(f"the brief's font '{font}' is not one of {', '.join(DISPLAY_FONTS)}")
    zones = _format(format_key)["zones"]
    layers = []
    for role, _ in ROLES:
        words = getattr(copy, role)
        if not words:
            continue
        x, y, w, h, size, align, face = zones[role]
        pill = role == "cta"
        layers.append(
            TextLayer(
                role=role,
                text=words,
                box=(x, y, w, h),
                font=font if face == "display" else "body-sans",
                size=size,
                color=readable_on(accent_color) if pill else (accent_color if role == "offer" else text_color),
                align=align,
                kind="pill" if pill else "text",
                fill=accent_color if pill else "",
            )
        )
    return tuple(layers)

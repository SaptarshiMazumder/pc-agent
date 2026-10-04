"""A text ad's words as LAYERS over a clean picture — what makes them editable.

In overlay mode the image model makes the picture with no text; the words are set on top by code,
each piece of copy one layer: its words, the area it sits in, the font, the size, the colour. The
picture (`base`) is kept, so changing a word, moving a block or picking another colour re-draws
the design from the base and the layers — free, instant, and exact to the letter.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, replace

# The bundled fonts (fonts/, SIL Open Font License): a style name -> its file.
FONTS = {
    "bold-sans": "Inter-ExtraBold.ttf",
    "body-sans": "Inter-Regular.ttf",
    "condensed": "Oswald-Bold.ttf",
    "elegant-serif": "PlayfairDisplay-Bold.ttf",
}
DISPLAY_FONTS = ("bold-sans", "condensed", "elegant-serif")  # what a brief may choose for its headline
ALIGNS = ("left", "center", "right")
KINDS = ("text", "pill")  # pill: the words on a filled rounded bar (a button)
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


@dataclass(frozen=True)
class TextLayer:
    role: str  # headline / subline / offer / cta / fine_print
    text: str
    box: tuple[float, float, float, float]  # x, y, width, height — fractions of the frame
    font: str  # a key of FONTS
    size: float  # the largest letter size, as a fraction of the frame's height; shrunk to fit the box
    color: str  # "#rrggbb"
    align: str = "center"
    kind: str = "text"
    fill: str = ""  # a pill's colour

    def __post_init__(self) -> None:
        x, y, w, h = self.box
        if not (0 <= x and 0 <= y and w > 0 and h > 0 and x + w <= 1.0001 and y + h <= 1.0001):
            raise ValueError(f"{self.role}: its area {self.box} must lie inside the frame")
        if self.font not in FONTS:
            raise ValueError(f"{self.role}: font '{self.font}' is not one of {', '.join(FONTS)}")
        if not 0 < self.size <= 0.5:
            raise ValueError(f"{self.role}: size {self.size} must be a fraction of the frame's height, up to 0.5")
        if not HEX.match(self.color) or (self.fill and not HEX.match(self.fill)):
            raise ValueError(f"{self.role}: colours are #rrggbb, not '{self.color}' / '{self.fill}'")
        if self.align not in ALIGNS or self.kind not in KINDS:
            raise ValueError(f"{self.role}: align is {'/'.join(ALIGNS)} and kind {'/'.join(KINDS)}")
        if self.kind == "pill" and not self.fill:
            raise ValueError(f"{self.role}: a pill needs its fill colour")

    @classmethod
    def from_dict(cls, data: dict) -> "TextLayer":
        return cls(
            role=str(data["role"]),
            text=str(data.get("text") or ""),
            box=tuple(float(v) for v in data["box"]),  # type: ignore[arg-type]
            font=str(data["font"]),
            size=float(data["size"]),
            color=str(data["color"]),
            align=str(data.get("align") or "center"),
            kind=str(data.get("kind") or "text"),
            fill=str(data.get("fill") or ""),
        )

    def to_dict(self) -> dict:
        return {**asdict(self), "box": list(self.box)}


@dataclass(frozen=True)
class PosterText:
    base: str  # the clean picture the layers are drawn on
    layers: tuple[TextLayer, ...]

    def with_words(self, words: dict[str, str]) -> "PosterText":
        """The same design with new words for the roles named; everything else kept."""
        unknown = [r for r in words if r not in {layer.role for layer in self.layers}]
        if unknown:
            raise ValueError(f"no text layer {', '.join(unknown)} (layers: {', '.join(l.role for l in self.layers)})")
        return replace(
            self, layers=tuple(replace(l, text=words[l.role]) if l.role in words else l for l in self.layers)
        )

    @classmethod
    def from_dict(cls, data: dict) -> "PosterText":
        return cls(base=str(data["base"]), layers=tuple(TextLayer.from_dict(l) for l in data.get("layers") or []))

    def to_dict(self) -> dict:
        return {"base": self.base, "layers": [l.to_dict() for l in self.layers]}

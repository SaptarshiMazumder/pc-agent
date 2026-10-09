"""The account's brand — what every post carries the same way: who it is, the line it signs off
with, its type and colours, and its DESIGN NOTES: the rules the user taught it ("never let a shape
touch her hair", "the sign-off gets its own space") — every design and every review follows them,
so a correction made once holds for every post after. Set in the Posts tab; the post planner, the
renderer and the designer read it."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
# The bundled fonts a brand may choose (fonts/, SIL Open Font License).
HEADING_FONTS = {
    "cormorant": "CormorantGaramond-SemiBold.ttf",
    "playfair": "PlayfairDisplay-Bold.ttf",
    "oswald": "Oswald-Bold.ttf",
    "inter": "Inter-ExtraBold.ttf",
}
LABEL_FONTS = {"manrope": "Manrope-Medium.ttf", "manrope-bold": "Manrope-Bold.ttf", "inter": "Inter-Regular.ttf"}


@dataclass(frozen=True)
class BrandProfile:
    name: str = ""  # the account's name, e.g. "The Finds Edit"
    handle: str = ""  # its Instagram handle, without @
    tagline: str = ""  # the line the last slide signs off with, e.g. "We find beautiful things for women."
    voice: str = ""  # how it talks, in a sentence, e.g. "a discovery page with taste: warm, a little fantasy, never salesy"
    # the line every caption ends with, in the user's words — "Independently curated. Not sponsored. …"
    caption_disclosure: str = ""
    heading_font: str = "cormorant"  # a key of fonts: cormorant | playfair | oswald | inter
    label_font: str = "manrope"  # manrope | inter
    text_color: str = "#ffffff"
    accent_color: str = "#f2c14e"
    design_notes: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        if self.heading_font not in HEADING_FONTS or self.label_font not in LABEL_FONTS:
            raise ValueError(
                f"brand fonts: heading one of {', '.join(HEADING_FONTS)}, label one of {', '.join(LABEL_FONTS)}"
            )
        for c in (self.text_color, self.accent_color):
            if not HEX.match(c):
                raise ValueError(f"brand colours are #rrggbb, not '{c}'")

    @classmethod
    def from_dict(cls, data: dict) -> "BrandProfile":
        known = {k: str(v) for k, v in data.items() if k in cls.__dataclass_fields__ and k != "design_notes" and v is not None}
        notes = tuple(" ".join(str(n).split()) for n in data.get("design_notes") or () if str(n).strip())
        return cls(**known, design_notes=notes)

    def font_file(self, face: str) -> str:
        return HEADING_FONTS[self.heading_font] if face == "heading" else LABEL_FONTS[self.label_font]

    def to_dict(self) -> dict:
        return {**asdict(self), "design_notes": list(self.design_notes)}

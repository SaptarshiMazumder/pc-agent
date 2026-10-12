"""One setting a template's step asks for — a CHOICE the user makes: who is in it, where, how many,
what size, on which model. Manual by default: with the campaign's Auto off, a required setting
nobody chose stops its step until the user chooses it; with Auto on, the agent fills it.

Each setting has a home that already exists:
    cast           the campaign's cast member
    direction      a field of the creative direction (`field`: location, pose, wardrobe, mood…);
                   settings that share a field are joined, so a template can ask for "drape" and
                   "framing" separately and both reach the brief
    count / model / seconds / resolution
                   the step's own fields

`suggested` is what Auto puts there when nothing was chosen; it is never used with Auto off.
`source` draws the options from a library instead of the template ("locations": the location
library; "category_poses": the product's category's poses) — see TemplateSettingSources.
A template declares a step's settings by name (a standard one below) or in full.
"""

from __future__ import annotations

from dataclasses import dataclass

KINDS = ("cast", "choice", "text", "number", "model", "aspect", "resolution")
HOMES = ("cast", "direction", "count", "model", "seconds", "resolution")
DIRECTION_FIELDS = ("wardrobe", "location", "time_of_day", "weather", "pose", "mood", "background", "aspect_ratio")
SOURCES = ("", "locations", "category_poses")


@dataclass(frozen=True)
class TemplateSetting:
    key: str
    label: str
    kind: str
    home: str
    field: str = ""  # direction: which field of the creative direction it fills
    options: tuple[str, ...] = ()  # choice / aspect / resolution: what it can be ("" = free text too)
    required: bool = False
    suggested: str = ""  # what Auto picks when nothing was chosen
    help: str = ""
    minimum: int = 0  # number
    maximum: int = 0  # number; 0 = no limit
    source: str = ""  # where the options come from when not the template (SOURCES)

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"setting {self.key}: kind '{self.kind}' is not one of {', '.join(KINDS)}")
        if self.home not in HOMES:
            raise ValueError(f"setting {self.key}: home '{self.home}' is not one of {', '.join(HOMES)}")
        if self.source not in SOURCES:
            raise ValueError(f"setting {self.key}: source '{self.source}' is not one of {', '.join(s for s in SOURCES if s)}")
        if self.home == "direction" and self.field not in DIRECTION_FIELDS:
            raise ValueError(f"setting {self.key}: a direction setting fills one of {', '.join(DIRECTION_FIELDS)}, not '{self.field}'")

    def check(self, value: str) -> str:
        """The value, cleaned — or ValueError saying what it may be."""
        value = str(value).strip()
        if not value:
            return ""
        if self.kind == "number":
            if not value.isdigit():
                raise ValueError(f"{self.label} is a number, not '{value}'")
            n = int(value)
            if n < self.minimum or (self.maximum and n > self.maximum):
                raise ValueError(f"{self.label} is {self.minimum}–{self.maximum or 'any'}, not {n}")
        elif self.kind in ("aspect", "resolution") and self.options and value not in self.options:
            raise ValueError(f"{self.label} is one of {', '.join(self.options)}, not '{value}'")
        return value

    @classmethod
    def from_dict(cls, data: dict) -> "TemplateSetting":
        return cls(
            key=str(data["key"]), label=str(data.get("label") or data["key"]), kind=str(data["kind"]),
            home=str(data["home"]), field=str(data.get("field") or ""),
            options=tuple(str(o) for o in data.get("options") or ()), required=bool(data.get("required")),
            suggested=str(data.get("suggested") or ""), help=str(data.get("help") or ""),
            minimum=int(data.get("minimum") or 0), maximum=int(data.get("maximum") or 0),
            source=str(data.get("source") or ""),
        )

    def to_dict(self) -> dict:
        return {
            "key": self.key, "label": self.label, "kind": self.kind, "home": self.home, "field": self.field,
            "options": list(self.options), "required": self.required, "suggested": self.suggested, "help": self.help,
            "minimum": self.minimum, "maximum": self.maximum, "source": self.source,
        }


ASPECTS = ("9:16", "4:5", "1:1", "16:9")
RESOLUTIONS = ("480p", "720p", "1080p")

# The settings most templates share, by name. A template may override any field of one.
STANDARD: dict[str, TemplateSetting] = {
    s.key: s
    for s in (
        TemplateSetting("cast", "Who is in it", "cast", "cast", required=True,
                        help="A member of your cast."),
        TemplateSetting("aspect_ratio", "Shape", "aspect", "direction", "aspect_ratio", ASPECTS, required=True,
                        suggested="9:16", help="9:16 for Reels and Stories, 4:5 for feed posts."),
        TemplateSetting("location", "Where", "choice", "direction", "location", required=True,
                        help="A place from the library, or your own words.", source="locations"),
        TemplateSetting("time_of_day", "Light", "choice", "direction", "time_of_day",
                        ("morning", "midday", "golden hour", "evening", "night"),
                        help="Leave empty and the brief decides."),
        TemplateSetting("mood", "Mood", "text", "direction", "mood", help="Leave empty and the brief decides."),
        TemplateSetting("pose", "Pose", "text", "direction", "pose", help="Leave empty and the brief decides."),
        TemplateSetting("wardrobe", "Wearing with it", "text", "direction", "wardrobe",
                        help="What else the model wears. Leave empty and the brief decides."),
        TemplateSetting("background", "Background", "text", "direction", "background",
                        help="For product-only shots: the surface, box or backdrop."),
        TemplateSetting("count", "How many", "number", "count", required=True, suggested="3", minimum=1, maximum=4),
        TemplateSetting("image_model", "Model", "model", "model", required=True, help="Which image model makes them."),
        TemplateSetting("video_model", "Model", "model", "model", required=True, help="Which video model makes it."),
        TemplateSetting("seconds", "Length (s)", "number", "seconds", required=True, suggested="5", minimum=3, maximum=15),
        TemplateSetting("resolution", "Resolution", "resolution", "resolution", RESOLUTIONS, required=True, suggested="720p"),
    )
}

# What a step asks for when its template says nothing (and for steps the user adds).
BY_ACTION: dict[str, tuple[str, ...]] = {
    "brief": ("cast", "aspect_ratio", "location", "time_of_day", "mood"),
    "sheet": ("image_model",),
    "product_sheet": ("image_model",),
    "images": ("count", "image_model"),
    "video": ("video_model", "seconds", "resolution"),
}


def resolve(entry: str | dict) -> TemplateSetting:
    """A template's setting entry: a standard name, or a full or partial declaration (a partial one
    names a standard `key` and overrides some of its fields)."""
    if isinstance(entry, str):
        if entry not in STANDARD:
            raise ValueError(f"no standard setting '{entry}' (standard: {', '.join(STANDARD)})")
        return STANDARD[entry]
    base = STANDARD.get(str(entry.get("key") or ""))
    merged = {**(base.to_dict() if base else {}), **entry}
    return TemplateSetting.from_dict(merged)

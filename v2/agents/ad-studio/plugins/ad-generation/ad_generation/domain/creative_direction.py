"""The user's creative direction for one ad — every field optional.

A field the user gives is a REQUIREMENT: the brief takes it verbatim. A field left empty is the
brief writer's to choose, to suit the product. `notes` is anything else they said, in their words. The copy fields are a text ad's words, exact.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from ad_generation.domain.poster_copy import PosterCopy
from ad_generation.domain.shot_checklist import LOOK_WITH_CAST


@dataclass(frozen=True)
class CreativeDirection:
    wardrobe: str = ""
    location: str = ""
    time_of_day: str = ""
    weather: str = ""
    pose: str = ""
    mood: str = ""
    background: str = ""
    notes: str = ""
    # a text ad's copy, in the user's exact words
    headline: str = ""
    subline: str = ""
    offer: str = ""
    cta: str = ""
    fine_print: str = ""

    def given(self) -> dict[str, str]:
        """The fields the user actually set — the requirements."""
        return {k: v for k, v in asdict(self).items() if v.strip()}

    def copy(self) -> PosterCopy:
        """The copy the user gave for a text ad."""
        return PosterCopy.from_dict(asdict(self))

    def apply_to_look(self, look: dict) -> dict:
        """The brief's look with every given look field taken verbatim — so a requirement holds
        whatever the writer answered."""
        out = dict(look)
        for k in LOOK_WITH_CAST:
            value = getattr(self, k).strip()
            if value:
                out[k] = value
        return out

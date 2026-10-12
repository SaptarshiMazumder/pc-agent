"""An occasion sellers post for — DATA, a JSON file (occasions/<key>.json): its dates, how early
to start posting, where it is celebrated, what people buy and wear for it, the story themes that
suit it, and the usual offers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class Occasion:
    key: str
    name: str
    dates: tuple[date, ...]  # each year's main day
    lead_days: int  # start posting this many days before
    regions: tuple[str, ...]  # () = all India
    wear: str  # what people wear and buy — colours, fabrics, styles
    jewellery: str
    themes: tuple[str, ...]  # story theme keys that suit it, best first
    offers: str
    notes: str

    def next_date(self, today: date) -> date | None:
        return next((d for d in sorted(self.dates) if d >= today), None)

    def is_on(self, today: date) -> bool:
        """Within its posting window now: from `lead_days` before it to the day itself."""
        nxt = self.next_date(today)
        return nxt is not None and today >= nxt - timedelta(days=self.lead_days)

    @classmethod
    def from_dict(cls, key: str, data: dict) -> "Occasion":
        if not data.get("name") or not data.get("dates"):
            raise ValueError(f"occasion {key} needs a name and dates")
        return cls(
            key=key, name=str(data["name"]), dates=tuple(date.fromisoformat(str(d)) for d in data["dates"]),
            lead_days=int(data.get("lead_days") or 21), regions=tuple(str(r) for r in data.get("regions") or ()),
            wear=str(data.get("wear") or ""), jewellery=str(data.get("jewellery") or ""),
            themes=tuple(str(t) for t in data.get("themes") or ()), offers=str(data.get("offers") or ""),
            notes=str(data.get("notes") or ""),
        )

    def to_dict(self, today: date | None = None) -> dict:
        nxt = self.next_date(today) if today else None
        return {
            "key": self.key, "name": self.name, "dates": [d.isoformat() for d in self.dates], "lead_days": self.lead_days,
            "regions": list(self.regions), "wear": self.wear, "jewellery": self.jewellery, "themes": list(self.themes),
            "offers": self.offers, "notes": self.notes, "next": nxt.isoformat() if nxt else "",
        }

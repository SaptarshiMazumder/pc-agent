"""The words on a text ad — each one exact, because the image model must render it letter for
letter and the checker reads it back.

The user's own words are taken verbatim. The writer may fill the headline, the subline and the
call to action when the user gave none; an OFFER and FINE PRINT come only from the user — an ad
that promises a discount nobody gave is a false ad.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

# (field, how it is set on the poster) — in reading order.
ROLES = (
    ("headline", "the headline, the largest text"),
    ("subline", "the subline, smaller, under the headline"),
    ("offer", "the offer, bold and easy to spot"),
    ("cta", "the call to action, on a button or a bar"),
    ("fine_print", "the fine print, small, at the bottom"),
)
WRITER_MAY_FILL = ("headline", "subline", "cta")
REQUIRED = ("headline", "cta")


@dataclass(frozen=True)
class PosterCopy:
    headline: str = ""
    subline: str = ""
    offer: str = ""
    cta: str = ""
    fine_print: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "PosterCopy":
        return cls(**{k: str(data.get(k) or "").strip() for k, _ in ROLES})

    def merged(self, written: dict) -> "PosterCopy":
        """The user's copy, with the writer's words only where the user gave none and the writer
        is allowed to write (never an offer or fine print)."""
        out = asdict(self)
        for k in WRITER_MAY_FILL:
            if not out[k]:
                out[k] = str(written.get(k) or "").strip()
        return PosterCopy(**out)

    def missing(self) -> list[str]:
        return [f"copy.{k}" for k in REQUIRED if not getattr(self, k)]

    def lines(self) -> tuple[str, ...]:
        """Every piece of text, exactly — what the checker reads back."""
        return tuple(getattr(self, k) for k, _ in ROLES if getattr(self, k))

    def placed(self) -> str:
        """Each piece of text with its role, one per line, for the prompt."""
        return "\n".join(f'- {role}: "{getattr(self, k)}"' for k, role in ROLES if getattr(self, k))

    def to_dict(self) -> dict:
        return asdict(self)

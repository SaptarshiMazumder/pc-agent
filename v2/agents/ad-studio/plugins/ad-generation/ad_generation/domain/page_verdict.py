"""Whether one designed page is the page the post's plan asked for — and, when not, why."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PageVerdict:
    page: int
    matches: bool
    same_picture: bool
    missing_words: tuple[str, ...]
    notes: str  # what the page actually shows

    def line(self) -> str:
        if self.matches:
            return f"page {self.page}: matches its slide — {self.notes}"
        why = [] if self.same_picture else ["not the slide's photo"]
        if self.missing_words:
            why.append("missing the words " + ", ".join(f"'{w}'" for w in self.missing_words))
        return f"page {self.page}: REFUSED ({'; '.join(why)}) — {self.notes}"

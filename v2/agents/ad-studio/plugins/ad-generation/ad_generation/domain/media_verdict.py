"""A quality check's answer for one still or clip."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class MediaVerdict:
    path: str
    score: int  # 1-10: would this stop a scroll and sell the product — for the user's eye
    product_exact: bool  # every must-keep detail present and right
    identity_kept: bool  # the cast member is recognisably the same person
    problems: tuple[str, ...]  # what to fix, concretely — becomes the next prompt's correction
    text_exact: bool = True  # a text ad: every word of its copy rendered exactly (else True)

    @property
    def passed(self) -> bool:
        """The product is exact and the person is the same — the two things a viewer would call
        wrong. The score is never a bar: the user judges taste, with the score beside
        each still, and decides what is good enough."""
        return self.product_exact and self.identity_kept and self.text_exact

    @classmethod
    def from_dict(cls, data: dict, path: str) -> "MediaVerdict":
        """The checker's answer."""
        for k in ("score", "product_exact", "identity_kept"):
            if k not in data:
                raise ValueError(f"the check left out '{k}'")
        return cls(
            path=path,
            score=int(data["score"]),
            product_exact=bool(data["product_exact"]),
            identity_kept=bool(data["identity_kept"]),
            problems=tuple(str(p).strip() for p in data.get("problems") or [] if str(p).strip()),
            text_exact=bool(data.get("text_exact", True)),
        )

    @classmethod
    def load(cls, data: dict) -> "MediaVerdict":
        """A recorded verdict. `passed` is not read back: it follows from the facts, by the rule
        in force now."""
        return cls.from_dict(data, path=str(data["path"]))

    def to_dict(self) -> dict:
        return {**asdict(self), "passed": self.passed}

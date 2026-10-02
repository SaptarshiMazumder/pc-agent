"""The recurring AI models — kept across campaigns, so the same faces carry the account."""

from __future__ import annotations

from typing import Protocol

from ad_generation.domain.cast_member import CastMember


class CastLibrary(Protocol):
    def all(self) -> list[CastMember]: ...

    def get(self, name: str) -> CastMember:
        """Raises KeyError naming the cast there is."""
        ...

    def sheet_stem(self, name: str) -> str:
        """The workspace path stem a new character sheet for `name` is saved under."""
        ...

    def save(self, member: CastMember) -> None: ...

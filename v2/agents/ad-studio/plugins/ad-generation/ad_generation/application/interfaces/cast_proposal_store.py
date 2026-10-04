"""Cast members the agent proposed and the user has not made yet, and the one-time approvals the
studio's clicks recorded — kept with the cast, since a cast member belongs to no campaign."""

from __future__ import annotations

from typing import Protocol


class CastProposalStore(Protocol):
    def proposals(self) -> dict[str, dict]:
        """name -> {name, description, references, model}."""
        ...

    def save_proposals(self, proposals: dict[str, dict]) -> None: ...

    def approvals(self) -> list[dict]:
        """[{token, name, args}] — oldest first."""
        ...

    def save_approvals(self, approvals: list[dict]) -> None: ...

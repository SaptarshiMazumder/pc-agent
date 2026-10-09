"""Things the agent proposed and the user has not made yet, and the
one-time approvals the studio's clicks recorded — kept outside any campaign."""

from __future__ import annotations

from typing import Protocol


class ProposalStore(Protocol):
    def proposals(self) -> dict[str, dict]:
        """key -> what would be made (always with its `model`)."""
        ...

    def save_proposals(self, proposals: dict[str, dict]) -> None: ...

    def approvals(self) -> list[dict]:
        """[{token, key, model}] — oldest first."""
        ...

    def save_approvals(self, approvals: list[dict]) -> None: ...

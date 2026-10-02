"""The ad formats a brief can follow."""

from __future__ import annotations

from typing import Protocol

from ad_generation.domain.ad_format import AdFormat


class FormatLibrary(Protocol):
    def all(self) -> list[AdFormat]: ...

    def get(self, key: str) -> AdFormat:
        """Raises KeyError naming the known formats."""
        ...

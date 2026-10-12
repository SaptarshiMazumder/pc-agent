"""The user's own files in the library (generations are read from their campaigns)."""

from __future__ import annotations

from typing import Protocol

from ad_generation.domain.library_item import LibraryItem


class LibraryStore(Protocol):
    def uploads(self) -> list[LibraryItem]:
        """The user's uploads, newest first."""
        ...

    def add_upload(self, src: str, label: str) -> LibraryItem:
        """Copy a workspace file (an upload) into the library under `label` (may be '') -> its item.
        Raises ValueError for a file that is missing, outside the workspace, or not an image or clip."""
        ...

    def remove_upload(self, path: str) -> None:
        """Raises KeyError when it is not one of the user's uploads."""
        ...

"""The account's collections — images and clips gathered from campaigns for posts."""

from __future__ import annotations

from typing import Protocol

from ad_generation.domain.collection import Collection


class CollectionStore(Protocol):
    def all(self) -> list[Collection]: ...

    def get(self, slug: str) -> Collection:
        """Raises KeyError naming the collections there are."""
        ...

    def find(self, name_or_slug: str) -> Collection | None: ...

    def new_slug(self, name: str) -> str: ...

    def save(self, collection: Collection) -> None: ...

    def delete(self, slug: str) -> None: ...

    def import_file(self, slug: str, src: str) -> str:
        """Copy one of the user's own files (an upload) into the collection -> its workspace path.
        Raises when there is no such file."""
        ...

"""The design references: pictures of designs to follow, each with what a designer reads off it."""

from __future__ import annotations

from typing import Protocol

from ad_generation.domain.design_reference import DesignReference


class DesignReferenceStore(Protocol):
    def all(self) -> list[DesignReference]:
        """Newest first."""
        ...

    def get(self, slug: str) -> DesignReference:
        """Raises KeyError naming the references there are."""
        ...

    def new_slug(self, name: str) -> str:
        """A slug for a reference called `name`, not yet taken."""
        ...

    def import_image(self, slug: str, src: str, crop: tuple[int, int, int, int] | None) -> str:
        """Copy a workspace picture in as the reference's picture — cut to `crop` (x, y, width,
        height in the picture's pixels) when given, to keep one design out of a page of them —
        and return its workspace path. Raises ValueError for a file outside the workspace, not a
        picture, or a crop outside it."""
        ...

    def fingerprint(self, image: str) -> str:
        """A fingerprint of a kept picture: equal for the same picture."""
        ...

    def save(self, reference: DesignReference) -> None:
        ...

    def delete(self, slug: str) -> None:
        """Raises KeyError when there is no such reference."""
        ...

    def discard(self, slug: str) -> None:
        """Drop what `import_image` kept for a reference that was never saved (it was refused)."""
        ...

"""Posts — their plans, and the files a render makes."""

from __future__ import annotations

from typing import Protocol

from ad_generation.domain.design_progress import DesignProgress
from ad_generation.domain.post import Post


class PostStore(Protocol):
    def all(self) -> list[Post]: ...

    def get(self, slug: str) -> Post:
        """Raises KeyError naming the posts there are."""
        ...

    def new_slug(self, name: str) -> str: ...

    def save(self, post: Post) -> None: ...

    def out_dir(self, slug: str) -> str:
        """An empty folder (workspace path) for a render's files; the last render's are removed."""
        ...

    def stage_files(self, slug: str, files: list[str]) -> list[str]:
        """Files made elsewhere (a design tool's downloads; a .zip of pages is opened, its pages in
        name order) copied into a STAGING folder, numbered in order -> their paths. The post's own
        files are untouched until `promote`. Raises on a missing file or one Instagram does not
        take."""
        ...

    def same_as_render(self, slug: str, staged: list[str]) -> bool:
        """Whether any staged page is byte-for-byte one of the post's current output files."""
        ...

    def promote(self, slug: str) -> list[str]:
        """The staged files become the post's output (the last output is replaced) -> their paths."""
        ...

    def discard(self, slug: str) -> None:
        """Drop the staged files."""
        ...

    def write_design(self, slug: str, n: int, html: str) -> str:
        """Keep slide `n`'s design (HTML) -> its workspace path."""
        ...

    def save_design_progress(self, progress: DesignProgress) -> None:
        """Where the post's design run is, slide by slide — written as it moves."""
        ...

    def design_progress(self, slug: str) -> DesignProgress | None:
        """The last design run's progress; None when the post was never designed."""
        ...

    def design_preview(self, slug: str, n: int) -> str:
        """Where slide `n`'s design preview goes (workspace path)."""
        ...

    def scratch(self, name: str) -> str:
        """A folder (workspace path) for working files that are not part of any post."""
        ...

    def bundle(self, slug: str, files: list[str], caption: str) -> tuple[str, str]:
        """caption.txt and a zip of the files and the caption -> (caption path, zip path)."""
        ...

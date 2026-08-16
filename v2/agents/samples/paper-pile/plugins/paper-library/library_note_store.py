"""LibraryNoteStore — the notes on disk, parsed.

THE FORMAT IS PARSED, NOT GUESSED. `parse` reads exactly the header keys `ingest-a-document`
tells the agent to write. A note the parser cannot read still appears (title falls back to the
file name) rather than vanishing: a library that silently drops documents is worse than one
showing a badly-titled entry.

The store owns note IO and nothing else — the tools above it decide what to do with the result.
"""

from __future__ import annotations

import re
from pathlib import Path

from agent_runtime.application.run_context import current_workspace

#: Where notes live, relative to the agent's workspace. One folder, flat: a library that needs a
#: directory tree needs a database, and this is neither (the database is for the text, not the
#: filing).
LIBRARY_DIR = "library"

#: Copies of ingested source files, named after the note that describes them.
SOURCES_DIR = "library/sources"

#: The index lives beside the notes it describes, so a backup of the library takes it too.
DATABASE_PATH = "library/index.db"

#: Header keys the ingest skill writes. Anything else in the header is preserved on disk and
#: ignored here — the tools do not own the note, the agent does.
HEADER_KEYS = ("title", "source", "added", "tags")

_LINK = re.compile(r"\[\[([^\]]+)\]\]")


def library_root() -> Path:
    return Path(current_workspace(".")) / LIBRARY_DIR


def sources_root() -> Path:
    return Path(current_workspace(".")) / SOURCES_DIR


def database_path() -> Path:
    return Path(current_workspace(".")) / DATABASE_PATH


class LibraryNoteStore:
    """Reads the markdown notes. Never writes them — the agent writes notes with `write`, so
    there is exactly one way a note comes into being and one format to keep in step."""

    def parse(self, path: Path) -> dict:
        """One note -> {title, source, added, tags, body}. Never raises: an unreadable note
        degrades to a titled placeholder rather than taking down a listing of forty."""
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return {"title": path.stem, "source": "", "added": "", "tags": [], "body": ""}

        header: dict = {}
        body = text
        # A leading `---` block is the header. Absent is fine and common for a hand-written note.
        if text.startswith("---"):
            _, _, rest = text.partition("\n")
            raw_header, sep, raw_body = rest.partition("\n---")
            if sep:
                body = raw_body.lstrip("\n")
                for line in raw_header.splitlines():
                    key, colon, value = line.partition(":")
                    key = key.strip().lower()
                    if colon and key in HEADER_KEYS:
                        header[key] = value.strip()

        tags = [t.strip() for t in header.get("tags", "").replace(",", " ").split() if t.strip()]
        return {
            "title": header.get("title") or path.stem.replace("-", " "),
            "source": header.get("source", ""),
            "added": header.get("added", ""),
            "tags": tags,
            "body": body,
        }

    def all(self) -> list[tuple[Path, dict]]:
        root = library_root()
        if not root.is_dir():
            return []
        return [(path, self.parse(path)) for path in sorted(root.glob("*.md"))]

    def links_in(self, body: str) -> list[str]:
        return [m.strip().removesuffix(".md") for m in _LINK.findall(body)]

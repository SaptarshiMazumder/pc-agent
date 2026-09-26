"""The Library's catalogue, read. `library/index.json` — one record per item — and the files
each record points at.

READ-ONLY, ON PURPOSE. The window is the index's only writer: every way into the Library is a
user act (a drop on the tab, a Save button, a chip after a run), and the agent never invents an
entry. One writer means no merge to get wrong, and "what is in my Library" is always exactly what
the person put there. The plugin reads the catalogue and copies files OUT of it into the chat.

TOLERANT OF NOTHING BEING THERE. A workspace with no Library, or an index that will not parse,
is an empty Library — the tools say so in words rather than failing. A record whose files are
missing is reported as such, not skipped, because "I saved it and it is not there" is a thing
the user needs to hear.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import library_paths


@dataclass
class LibraryItem:
    id: str
    kind: str
    origin: str
    name: str
    #: Library-relative posix path: a folder for a workflow, a file for the other kinds.
    path: str
    note: str = ""
    versions: list[dict] = field(default_factory=list)
    #: {"chat": <session folder>, "title": <chat title>} for a saved item; empty when uploaded.
    from_chat: dict = field(default_factory=dict)
    created: str = ""

    @property
    def latest_version(self) -> int:
        vs = [int(v.get("v") or 0) for v in self.versions if isinstance(v, dict)]
        return max(vs) if vs else 0

    def version_entry(self, v: int) -> dict:
        for entry in self.versions:
            if isinstance(entry, dict) and int(entry.get("v") or 0) == v:
                return entry
        return {}

    def one_line(self) -> str:
        """`id · kind · origin · name — note (from: chat) [vN]` — the find tool's row."""
        bits = [self.id, self.kind, self.origin, self.name]
        line = "  ".join(bits)
        if self.note:
            line += f" — {self.note}"
        if self.from_chat.get("title"):
            line += f" (from: {self.from_chat['title']})"
        if self.kind == "workflow" and self.latest_version:
            line += f" [v{self.latest_version}]"
        return line


class LibraryIndex:
    """The catalogue for one workspace."""

    def __init__(self, root: Path, items: list[LibraryItem], problem: str = "") -> None:
        self.root = Path(root)
        self._items = items
        #: Why the index could not be read, when it could not; "" otherwise.
        self.problem = problem

    @classmethod
    def load(cls, root: Path) -> "LibraryIndex":
        p = library_paths.index_path(root)
        if not p.exists():
            return cls(root, [], "")
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            return cls(root, [], f"library/index.json could not be read: {e}")
        items: list[LibraryItem] = []
        for raw in (data.get("items") if isinstance(data, dict) else None) or []:
            if not isinstance(raw, dict):
                continue
            kind = str(raw.get("kind") or "")
            origin = str(raw.get("origin") or "")
            if kind not in library_paths.KINDS or origin not in library_paths.ORIGINS:
                continue
            items.append(
                LibraryItem(
                    id=str(raw.get("id") or ""),
                    kind=kind,
                    origin=origin,
                    name=str(raw.get("name") or ""),
                    path=str(raw.get("path") or "").strip("/"),
                    note=str(raw.get("note") or ""),
                    versions=[v for v in (raw.get("versions") or []) if isinstance(v, dict)],
                    from_chat=dict(raw.get("from") or {}),
                    created=str(raw.get("created") or ""),
                )
            )
        return cls(root, [i for i in items if i.id and i.name and i.path], "")

    @property
    def items(self) -> list[LibraryItem]:
        return list(self._items)

    def find(self, kind: str = "", query: str = "") -> list[LibraryItem]:
        """Items of a kind (or all), whose name/note/origin-chat mention `query` (or all)."""
        q = (query or "").strip().lower()
        out = []
        for it in self._items:
            if kind and it.kind != kind:
                continue
            if q and q not in " ".join(
                (it.name, it.note, str(it.from_chat.get("title") or ""), it.id)
            ).lower():
                continue
            out.append(it)
        return out

    def resolve(self, ref: str) -> tuple[LibraryItem | None, str]:
        """One item for an id or a name — or None and the reason (nothing / several)."""
        r = (ref or "").strip()
        if not r:
            return None, "name the item — its id or its name from library_find"
        for it in self._items:
            if it.id == r:
                return it, ""
        by_name = [it for it in self._items if it.name.lower() == r.lower()]
        if len(by_name) == 1:
            return by_name[0], ""
        if len(by_name) > 1:
            return None, (
                f"'{r}' names {len(by_name)} items — use the id: "
                + ", ".join(f"{it.id} ({it.kind}, {it.origin})" for it in by_name)
            )
        return None, f"no Library item called '{r}' — library_find lists what there is"

    # ------------------------------------------------------------------ files

    def files_of(self, item: LibraryItem, version: int | None = None) -> tuple[list[Path], str]:
        """The files behind an item (a workflow's chosen version, else the one file), and a
        problem string when they are not on disk.

        A REFERENCE IS NEVER CHECKED. The sandbox is handed the catalogue, the workflows and
        the files (plugin.toml) and deliberately not the media — so on a hosted daemon a saved
        face is not on this side's disk even though it exists. The catalogue is the truth for a
        reference; the window, which has the real workspace, does the copy."""
        base = library_paths.item_path(self.root, item.path)
        if item.kind == "reference":
            return [base], ""
        if item.kind == "template":
            files = sorted(p for p in base.rglob("*") if p.is_file()) if base.is_dir() else []
            if not files:
                return [], f"template {item.name} has no files under {library_paths.library_rel(item.path)}"
            return files, ""
        if item.kind == "workflow":
            v = int(version or item.latest_version or 0)
            folder = base / f"v{v}" if v else base
            if not folder.is_dir():
                return [], (
                    f"{item.name} v{v} has no files on disk under "
                    f"{library_paths.library_rel(item.path)} — the Library tab may still be "
                    "saving it, or it was deleted"
                )
            files = sorted(p for p in folder.iterdir() if p.is_file())
            return files, "" if files else f"{item.name} v{v} is an empty folder"
        if not base.is_file():
            return [], f"{library_paths.library_rel(item.path)} is not on disk"
        return [base], ""

    @staticmethod
    def api_graph_file(files: list[Path]) -> Path | None:
        """The `.api.json` among a version's files, if any."""
        for p in files:
            if p.name.endswith(".api.json"):
                return p
        return None

    @staticmethod
    def ui_graph_file(files: list[Path]) -> Path | None:
        """The ComfyUI-editor `.json` (not the api one) among a version's files, if any."""
        for p in files:
            if p.suffix == ".json" and not p.name.endswith(".api.json") and not p.name.endswith(
                ".manifest.json"
            ):
                return p
        return None

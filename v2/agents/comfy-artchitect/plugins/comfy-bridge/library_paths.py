"""Where the Library lives inside the workspace, and what its parts are called. Pure.

THE LIBRARY IS THE ONE FOLDER CHATS SHARE. Every chat keeps its own `references/<chat>`,
`workflows/<chat>` and `outputs/<chat>` (chat_paths) and reads nobody else's; the Library is the
place the USER put things to keep — a workflow worth running again, a face, a product photo —
and it is the only thing a later chat may reach for. It sits inside the workspace so the daemon's
workspace RPCs already list, upload and delete in it, and so the sandbox can ship the parts the
agent reads (see plugin.toml `[sandbox].workspace`).

TWO ORIGINS, BY WHO PUT IT THERE. `uploaded/` is what the person brought from their own machine;
`saved/` is what came out of a chat (the workspace's "Add to Library", a card's "Save to
Library", the chip after a run). The split is what the Library tab shows as its two sections,
and it is a folder rather than a flag so a glance at the disk says the same thing the tab does.

THREE KINDS. A `workflow` is a folder of versions (`v1/`, `v2/`) each holding the api.json, the
ui json and the installer files that existed when it was saved; a `reference` and a `file` are
one file each. `index.json` is the catalogue — one record per item, written only by the window.
"""

from __future__ import annotations

from pathlib import Path

LIBRARY = "library"
INDEX_NAME = "index.json"
INDEX = f"{LIBRARY}/{INDEX_NAME}"

ORIGINS = ("uploaded", "saved")
KINDS = ("workflow", "reference", "file")
#: The folder each kind lives under, inside an origin.
KIND_DIRS = {"workflow": "workflows", "reference": "references", "file": "files"}


def library_dir(root: Path) -> Path:
    """`<workspace>/library`."""
    return Path(root) / LIBRARY


def index_path(root: Path) -> Path:
    """`<workspace>/library/index.json`."""
    return Path(root) / LIBRARY / INDEX_NAME


def item_path(root: Path, library_rel: str) -> Path:
    """An item's `path` from the index — library-relative posix — as a filesystem path."""
    return library_dir(root) / library_rel


def library_rel(library_rel: str) -> str:
    """The same path WORKSPACE-relative, the form everything that names a file to the window
    uses (`library/uploaded/references/face.png`)."""
    return f"{LIBRARY}/{library_rel.strip('/')}"

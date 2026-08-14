"""FolderBrowseTool — look inside a folder before scanning it.

FOR THE AGENT, NOT THE APP. The window uses the system folder dialog (`<input webkitdirectory>`),
because a hand-drawn directory browser is a worse imitation of a thing every user already knows.
This exists for the other half of the problem: when someone says "the papers on my desktop", the
agent has a place to look before it commits to scanning anything.

IT COUNTS WHAT MATTERS. Each child folder reports how many ingestible documents sit directly
inside it, so "which of these has the papers in it" is answerable without walking the whole tree.

THE SAME FENCE AS EVERYTHING ELSE. Every path goes through `check_read` before any IO — on a
hosted run a path into another tenant's subtree raises before this tool reads a byte.
"""

from __future__ import annotations

import json
import os
import string
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.write_scope import check_read

from folder_scan_tool import INGESTIBLE, SKIP_DIRS

#: Enough to fill a picker without stalling on a folder holding thousands of entries. Reported
#: when it bites — a truncated listing that says nothing is a listing that lies.
MAX_CHILDREN = 300


def _shallow_counts(folder: Path) -> tuple[int, int]:
    """(ingestible documents, subfolders) directly inside — never recursive. A recursive count
    would walk an entire drive to draw one row of a picker."""
    docs = subs = 0
    try:
        with os.scandir(folder) as it:
            for entry in it:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name.lower() not in SKIP_DIRS and not entry.name.startswith("."):
                            subs += 1
                    elif Path(entry.name).suffix.lower() in INGESTIBLE:
                        docs += 1
                except OSError:
                    continue
    except (OSError, PermissionError):
        return (0, 0)
    return (docs, subs)


class FolderBrowseTool(Tool):
    name = "library_browse"
    label = "Browse folders"
    default_retryable = True
    default_timeout_sec = 30.0
    description = (
        "List the sub-folders of a directory, with how many ingestible documents each one holds. "
        "Use it when the user names a place loosely ('the papers on my desktop') and you "
        "need to see what is there before scanning. Call with no path for the starting "
        "points (home folder and drives). Read-only: it never opens or ingests anything."
    )
    parameters = {
        "type": "object",
        "required": [],
        "properties": {
            "path": {
                "type": "string",
                "description": "Folder to list. Omit for the starting points (home + drives).",
            }
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            raw = str(params.get("path") or "").strip()
            if not raw:
                return ToolResult.text(json.dumps(self._roots(), indent=1))

            folder = check_read(Path(raw).expanduser())
            if not folder.exists():
                return ToolResult.text(f"no such folder: {folder}", is_error=True)
            if not folder.is_dir():
                return ToolResult.text(f"not a folder: {folder}", is_error=True)

            children = []
            capped = False
            try:
                entries = sorted(
                    (e for e in folder.iterdir() if not e.name.startswith(".")),
                    key=lambda e: e.name.lower(),
                )
            except PermissionError as e:
                # A folder the user cannot read is a normal thing to click on. Say so plainly
                # instead of returning an empty listing that reads as "this folder is empty".
                return ToolResult.text(f"cannot read {folder}: {e}", is_error=True)

            for entry in entries:
                if not entry.is_dir() or entry.name.lower() in SKIP_DIRS:
                    continue
                if len(children) >= MAX_CHILDREN:
                    capped = True
                    break
                docs, subs = _shallow_counts(entry)
                children.append(
                    {"name": entry.name, "path": str(entry), "documents": docs, "folders": subs}
                )

            here, _ = _shallow_counts(folder)
            out = {
                "path": str(folder),
                "parent": str(folder.parent) if folder.parent != folder else "",
                "documents_here": here,
                "folders": children,
            }
            if capped:
                out["CAPPED"] = f"showing the first {MAX_CHILDREN} sub-folders of more"
            return ToolResult.text(json.dumps(out, indent=1))
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"library_browse failed: {type(e).__name__}: {e}", is_error=True)

    def _roots(self) -> dict:
        """Where a picker opens: the user's own folders first, then whatever drives exist. Home
        before drives because the documents are nearly always under it."""
        roots = []
        home = Path.home()
        for name, path in (
            ("Home", home),
            ("Desktop", home / "Desktop"),
            ("Documents", home / "Documents"),
            ("Downloads", home / "Downloads"),
        ):
            if path.is_dir():
                docs, subs = _shallow_counts(path)
                roots.append(
                    {"name": name, "path": str(path), "documents": docs, "folders": subs}
                )
        if os.name == "nt":
            for letter in string.ascii_uppercase:
                drive = Path(f"{letter}:/")
                if drive.exists():
                    roots.append(
                        {"name": f"{letter}:", "path": str(drive), "documents": 0, "folders": 0}
                    )
        return {"path": "", "parent": "", "documents_here": 0, "folders": roots}

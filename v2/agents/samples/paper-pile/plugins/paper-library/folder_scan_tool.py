"""FolderScanTool — point the agent at a directory and let it find the work itself.

WHY THIS IS NOT "JUST USE ls". The question is never "what is in this folder", it is "what in
this folder do I still have to do". Answering that needs the index: a content hash per file, so a
second scan of the same directory reports nothing to do instead of re-reading forty documents.
Idempotence is the entire feature — without it nobody can point the agent at a folder twice, and
a folder that cannot be re-scanned cannot be watched on a heartbeat.

FOUR STATES, and they are not the same job:
  new        never seen             -> read it and write a note
  changed    seen, bytes differ     -> re-read it and REPLACE the note
  duplicate  same bytes, other path -> a copy of something already filed; skip, do not re-note
  indexed    seen, bytes identical  -> nothing to do

THE TENANT FENCE APPLIES. The folder path goes through `check_read` before any IO, exactly like
the core file tools: on a hosted run a path into another tenant's subtree raises before this tool
reads a single byte.

NO SILENT TRUNCATION. A capped scan says so in its result. "47 files" when there were 4,700 is a
lie the user would act on.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.write_scope import check_read

from library_database import LibraryDatabase
from library_note_store import database_path

#: What is worth offering to ingest. `extract_text` handles the office formats; the rest are read
#: as text. Anything else in the folder is not an error — it is simply not a document.
INGESTIBLE = {".pdf", ".docx", ".xlsx", ".pptx", ".txt", ".md", ".markdown", ".rst", ".htm", ".html"}

#: Caches and dev noise. Mirrors the core `find` tool's skip list.
SKIP_DIRS = {
    "node_modules",
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".idea",
    ".vscode",
    "appdata",
    "dist",
    "build",
    ".cache",
}

DEFAULT_MAX_FILES = 500


def sha256_of(path: Path) -> str:
    """Streamed, because a scan may cross a folder of 200 MB scans and reading each whole file
    into memory to hash it would be the slowest part of the tool."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class FolderScanTool(Tool):
    name = "library_scan"
    label = "Scan folder"
    default_retryable = True
    default_timeout_sec = 120.0
    description = (
        "Look in a FOLDER and report which documents there are new, changed, duplicates, or "
        "already in the library. Use this when the user points you at a directory instead of "
        "uploading files. It only reads and hashes — it ingests nothing — so it is safe to run "
        "on a big folder first to see the size of the job."
    )
    parameters = {
        "type": "object",
        "required": ["folder"],
        "properties": {
            "folder": {"type": "string", "description": "Directory to scan."},
            "recursive": {"type": "boolean", "description": "Descend into subfolders. Default true."},
            "max_files": {
                "type": "integer",
                "minimum": 1,
                "description": f"Safety cap. Default {DEFAULT_MAX_FILES}.",
            },
        },
    }

    def __init__(self, db: LibraryDatabase | None = None):
        self._db = db

    def _database(self) -> LibraryDatabase:
        # Late-bound: the workspace is a per-run value, so the path cannot be resolved at
        # registration time (one process serves several agents).
        db = self._db or LibraryDatabase(database_path())
        db.ensure_schema()
        return db

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            raw = str(params.get("folder") or "").strip()
            if not raw:
                return ToolResult.text("library_scan needs a `folder`", is_error=True)
            recursive = params.get("recursive")
            recursive = True if recursive is None else bool(recursive)
            cap = max(1, int(params.get("max_files") or DEFAULT_MAX_FILES))

            root = check_read(Path(raw).expanduser())
            if not root.exists():
                return ToolResult.text(f"no such folder: {root}", is_error=True)
            if not root.is_dir():
                return ToolResult.text(f"not a folder: {root}", is_error=True)

            db = self._database()
            buckets: dict[str, list] = {"new": [], "changed": [], "duplicate": [], "indexed": []}
            looked_at = 0
            capped = False

            for path in self._walk(root, recursive):
                if abort is not None and getattr(abort, "is_set", lambda: False)():
                    return ToolResult.text("library_scan aborted", is_error=True)
                if looked_at >= cap:
                    capped = True
                    break
                looked_at += 1
                try:
                    stat = path.stat()
                    state = db.state_of(str(path), sha256_of(path))
                except OSError as e:
                    # One unreadable file must not end the scan of the other four hundred — but
                    # it is reported, not dropped, so a permissions problem is visible.
                    buckets.setdefault("unreadable", []).append({"path": str(path), "why": str(e)})
                    continue
                buckets[state].append(
                    {
                        "path": str(path),
                        "name": path.name,
                        "size": stat.st_size,
                        "suggested_slug": self._slug(path.stem),
                    }
                )

            todo = len(buckets["new"]) + len(buckets["changed"])
            summary = {
                "folder": str(root),
                "files_examined": looked_at,
                "to_ingest": todo,
                "counts": {k: len(v) for k, v in buckets.items()},
                **buckets,
            }
            if capped:
                summary["CAPPED"] = (
                    f"stopped after {cap} files — there are more in this folder. Raise max_files "
                    f"or scan a subfolder; do NOT report this scan as complete."
                )
            if todo == 0 and not capped:
                extra = " (all already in the library)" if looked_at else ""
                summary["nothing_to_do"] = f"examined {looked_at} file(s), nothing new{extra}"
            return ToolResult.text(json.dumps(summary, indent=1))
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"library_scan failed: {type(e).__name__}: {e}", is_error=True)

    def _walk(self, root: Path, recursive: bool):
        if not recursive:
            for entry in sorted(root.iterdir()):
                if entry.is_file() and entry.suffix.lower() in INGESTIBLE:
                    yield entry
            return
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d.lower() not in SKIP_DIRS)
            for name in sorted(filenames):
                if Path(name).suffix.lower() in INGESTIBLE:
                    yield Path(dirpath) / name

    @staticmethod
    def _slug(stem: str) -> str:
        keep = [c.lower() if c.isalnum() else "-" for c in stem]
        slug = "".join(keep)
        while "--" in slug:
            slug = slug.replace("--", "-")
        return slug.strip("-") or "document"

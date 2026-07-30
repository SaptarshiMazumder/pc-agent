"""FileWorkspaceIndex — the default WorkspaceIndex adapter (a live directory scan).

Walks an agent's workspace (bounded + noise-filtered), classifies each file by extension,
reads a one-line summary for scripts/docs, and formats a manifest block for the prompt.
Behind the WorkspaceIndex port, so a richer index (descriptions, embeddings, persistence)
can replace it without touching the prompt or the gateway.
"""

from __future__ import annotations

import os
from pathlib import Path

from agent_runtime.domain.workspace import (
    KIND_DATA,
    KIND_DOC,
    KIND_IMAGE,
    KIND_OTHER,
    KIND_SCRIPT,
    WorkspaceResource,
)

# extension -> kind
_EXT_KIND = {
    **{
        e: KIND_SCRIPT
        for e in (
            ".py",
            ".js",
            ".mjs",
            ".ts",
            ".sh",
            ".bash",
            ".ps1",
            ".bat",
            ".cmd",
            ".rb",
            ".go",
            ".rs",
            ".php",
            ".pl",
            ".r",
        )
    },
    **{e: KIND_DOC for e in (".md", ".markdown", ".txt", ".rst")},
    **{
        e: KIND_IMAGE
        for e in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico", ".tiff")
    },
    **{
        e: KIND_DATA
        for e in (
            ".csv",
            ".tsv",
            ".json",
            ".ndjson",
            ".yaml",
            ".yml",
            ".xml",
            ".xlsx",
            ".xls",
            ".parquet",
            ".db",
            ".sqlite",
            ".sqlite3",
        )
    },
}

# directories never worth listing (caches, vcs, runtime state, big vendored trees)
_SKIP_DIRS = {
    "__pycache__",
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    "browser-profile",
    "sessions",
    ".agentd",
    ".idea",
    ".vscode",
    "dist",
    "build",
    ".cache",
    "skills",
    "tmp",  # the SCRATCH dir (cleanup.SCRATCH_DIRNAME): throwaway files, never indexed
}
_SKIP_SUFFIX = (".pyc", ".pyo", ".lock", ".tmp", ".log")
_SCAN_CAP = 5000  # hard ceiling on entries examined (protects huge dirs, e.g. home)
_SUMMARY_BYTES = 600
_COMMENT_PREFIXES = ("#", "//", "--", ";", "%")
_KIND_ORDER = (KIND_SCRIPT, KIND_DOC, KIND_DATA, KIND_IMAGE, KIND_OTHER)
_KIND_LABEL = {
    KIND_SCRIPT: "scripts",
    KIND_DOC: "docs",
    KIND_DATA: "data",
    KIND_IMAGE: "images",
    KIND_OTHER: "other",
}


def _human(size: int) -> str:
    f = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024 or unit == "GB":
            return f"{int(f)} B" if unit == "B" else f"{f:.1f} {unit}"
        f /= 1024
    return f"{int(size)} B"


def _summary(path: Path, kind: str) -> str:
    if kind not in (KIND_SCRIPT, KIND_DOC):
        return ""
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:_SUMMARY_BYTES]
    except OSError:
        return ""
    for line in head.splitlines():
        s = line.strip()
        if not s or s.startswith("#!"):  # skip blanks + shebang
            continue
        if kind == KIND_DOC:
            return s[:80]
        if s.startswith(_COMMENT_PREFIXES) or s.startswith('"""') or s.startswith("'''"):
            return s.lstrip("#/-;%\"' ").strip()[:80]
        return s[:80]  # first real line of code
    return ""


class FileWorkspaceIndex:
    def __init__(self, max_files: int = 100):
        self._max = max_files

    def scan(self, workspace: Path) -> list[WorkspaceResource]:
        root = Path(workspace)
        if not root.is_dir():
            return []
        try:  # never crawl the home folder (main's default)
            if root.resolve() == Path.home().resolve():
                return []
        except (OSError, RuntimeError):
            pass
        out: list[WorkspaceResource] = []
        examined = 0
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
            for name in sorted(filenames):
                examined += 1
                if examined > _SCAN_CAP or len(out) >= self._max:
                    return out
                if name.startswith(".") or name.endswith(_SKIP_SUFFIX):
                    continue
                p = Path(dirpath) / name
                try:
                    size = p.stat().st_size
                except OSError:
                    continue
                kind = _EXT_KIND.get(p.suffix.lower(), KIND_OTHER)
                rel = os.path.relpath(str(p), str(root)).replace("\\", "/")
                out.append(WorkspaceResource(rel, kind, size, _summary(p, kind)))
        return out

    def manifest(self, workspace: Path, agent_id: str = "") -> str:
        items = self.scan(workspace)
        if not items:
            return ""
        by_kind: dict[str, list[WorkspaceResource]] = {}
        for r in items:
            by_kind.setdefault(r.kind, []).append(r)
        lines = [
            "## Your workspace resources",
            "Files already in your working directory (read or run any with the file/exec "
            "tools; paths are relative to your workspace). You created or were given these - "
            "reuse them instead of recreating:",
        ]
        for kind in _KIND_ORDER:
            group = by_kind.get(kind)
            if not group:
                continue
            lines.append(f"**{_KIND_LABEL[kind]}**")
            for r in group:
                tail = f" - {r.summary}" if r.summary else ""
                lines.append(f"- {r.rel_path} ({_human(r.size)}){tail}")
        if len(items) >= self._max:
            lines.append(f"_(showing the first {self._max} files; more exist — use `ls`/`find`)_")
        return "\n".join(lines)


def build_workspace_index(config):
    """The WorkspaceIndex for the composition root (None unless the layer is enabled)."""
    if not getattr(config, "workspace_index_enabled", False):
        return None
    return FileWorkspaceIndex(max_files=getattr(config, "workspace_index_max_files", 100))

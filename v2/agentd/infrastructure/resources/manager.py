"""ResourceManager — the heart of the Resource Manager subsystem.

Reconciles the agent's workspace files with the cached index (the ResourceStore):
a new/changed file gets a fresh description from the (sync) describer and is cached;
a deleted file is dropped from the index. Produces the per-turn manifest (descriptions
included) AND the CRUD operations the resource tool exposes. An optional async ``rich_fn``
is the seam for vision/LLM captioning, invoked only on an explicit describe.

Composition over inheritance: it is handed a ResourceStore + ResourceDescriber (+ optional
rich_fn). Swap any of them without touching the tool or the prompt.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from agentd.domain.resource import (
    KIND_DATA, KIND_DOC, KIND_IMAGE, KIND_OTHER, KIND_SCRIPT, Resource, kind_for_path,
)

_SKIP_DIRS = {
    "__pycache__", ".git", ".hg", ".svn", "node_modules", ".venv", "venv",
    ".mypy_cache", ".pytest_cache", "browser-profile", "sessions", ".agentd",
    ".idea", ".vscode", "dist", "build", ".cache",
    "skills",   # the agent's skills live here but are advertised in the Skills section, not as resources
}
_SKIP_SUFFIX = (".pyc", ".pyo", ".lock", ".tmp", ".log")
_SCAN_CAP = 5000
_SAMPLE_BYTES = 8192
_KIND_ORDER = (KIND_SCRIPT, KIND_DOC, KIND_DATA, KIND_IMAGE, KIND_OTHER)
_KIND_LABEL = {KIND_SCRIPT: "scripts", KIND_DOC: "docs", KIND_DATA: "data",
               KIND_IMAGE: "images", KIND_OTHER: "other"}


def _human(size: int) -> str:
    f = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024 or unit == "GB":
            return f"{int(f)} B" if unit == "B" else f"{f:.1f} {unit}"
        f /= 1024
    return f"{int(size)} B"


def _sig(size: int, mtime: float) -> str:
    return f"{size}-{int(mtime)}"


def _is_home(p: Path) -> bool:
    """The user's HOME directory is never a curated agent workspace (it's `main`'s
    default). Indexing it would crawl AppData/OneDrive/etc. — slow + a privacy hole —
    so the manager skips it entirely."""
    try:
        return p.resolve() == Path.home().resolve()
    except (OSError, RuntimeError):
        return False


class ResourceManager:
    def __init__(self, store, describer, *, max_files: int = 100, rich_fn=None, enrich_max: int = 3):
        self._store = store
        self._describer = describer
        self._max = max_files
        self._rich_fn = rich_fn            # optional async (kind, path, bytes) -> str (vision/LLM)
        # background enrichment (the EXPENSIVE describe) — independent of the agent's turn
        self._enrich_max = enrich_max
        self._enrich_tasks: set = set()
        self._enriched: set = set()        # (agent_id, rel_path, sig) already enriched/queued
        self._sem = None                   # asyncio.Semaphore, lazily bound to the running loop

    # ---- describe one file (sync, cached) -----------------------------------

    def _index_one(self, agent_id: str, root: Path, p: Path) -> Resource:
        rel = os.path.relpath(str(p), str(root)).replace("\\", "/")
        st = p.stat()
        sig = _sig(st.st_size, st.st_mtime)
        cached = self._store.get(agent_id, rel)
        if cached and cached.sig == sig:
            return cached
        kind = kind_for_path(p)
        try:
            with p.open("rb") as f:
                sample = f.read(_SAMPLE_BYTES)
        except OSError:
            sample = b""
        desc = ""
        try:
            desc = self._describer.describe(kind, p, sample)
        except Exception:  # noqa: BLE001 — a bad describe never breaks the manifest
            desc = ""
        res = Resource(rel, kind, st.st_size, sig, desc, time.time())
        self._store.put(agent_id, res)
        return res

    # ---- reconcile disk <-> index -------------------------------------------

    def reconcile(self, workspace, agent_id: str) -> tuple[list[Resource], bool]:
        root = Path(workspace)
        if not root.is_dir() or _is_home(root):     # never crawl the home folder (main's default)
            return [], False
        out: list[Resource] = []
        examined = 0
        capped = False
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
            for name in sorted(filenames):
                examined += 1
                if examined > _SCAN_CAP or len(out) >= self._max:
                    capped = True
                    break
                if name.startswith(".") or name.endswith(_SKIP_SUFFIX):
                    continue
                p = Path(dirpath) / name
                if not p.is_file():
                    continue
                try:
                    out.append(self._index_one(agent_id, root, p))
                except OSError:
                    continue
            if capped:
                break
        if not capped:                       # drop index rows whose file is gone
            for r in self._store.list(agent_id):
                if not (root / r.rel_path).exists():
                    self._store.delete(agent_id, r.rel_path)
        for r in out:                        # expensive (vision/LLM) describe -> background, independent
            self.enqueue_enrich(workspace, agent_id, r)
        return out, capped

    # ---- the per-turn manifest (WorkspaceIndex port) ------------------------

    def manifest(self, workspace, agent_id: str = "") -> str:
        items, capped = self.reconcile(workspace, agent_id)
        if not items:
            return ""
        by_kind: dict[str, list[Resource]] = {}
        for r in items:
            by_kind.setdefault(r.kind, []).append(r)
        lines = [
            "## Your workspace resources",
            "Files in your working directory (read/run/edit/delete any with the resource or "
            "file/exec tools; paths are relative to your workspace). You created or were given "
            "these - reuse them instead of recreating:",
        ]
        for kind in _KIND_ORDER:
            group = by_kind.get(kind)
            if not group:
                continue
            lines.append(f"**{_KIND_LABEL[kind]}**")
            for r in group:
                tail = f" - {r.description}" if r.description else ""
                lines.append(f"- {r.rel_path} ({_human(r.size)}){tail}")
        if capped:
            lines.append(f"_(showing the first {self._max}; more exist - use `ls`/`find`)_")
        return "\n".join(lines)

    # ---- CRUD (the resource tool delegates here) ----------------------------

    def _safe(self, workspace, rel_path: str) -> Path:
        root = Path(workspace).resolve()
        p = (root / rel_path).resolve()
        if root != p and root not in p.parents:
            raise ValueError("path escapes the workspace")
        return p

    def create(self, workspace, agent_id: str, rel_path: str, content: str) -> Resource:
        p = self._safe(workspace, rel_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return self._index_one(agent_id, Path(workspace).resolve(), p)

    def read(self, workspace, rel_path: str) -> str:
        p = self._safe(workspace, rel_path)
        return p.read_text(encoding="utf-8", errors="replace")

    def edit(self, workspace, agent_id: str, rel_path: str, content: str) -> Resource:
        p = self._safe(workspace, rel_path)
        if not p.is_file():
            raise FileNotFoundError(rel_path)
        p.write_text(content, encoding="utf-8")
        return self._index_one(agent_id, Path(workspace).resolve(), p)

    def delete(self, workspace, agent_id: str, rel_path: str) -> bool:
        p = self._safe(workspace, rel_path)
        existed = p.is_file()
        if existed:
            p.unlink()
        self._store.delete(agent_id, rel_path)
        return existed

    def list_resources(self, workspace, agent_id: str) -> list[Resource]:
        items, _ = self.reconcile(workspace, agent_id)
        return items

    # ---- background enrichment (expensive describe, OFF the agent's path) ----

    def _semaphore(self) -> asyncio.Semaphore:
        if self._sem is None:                # lazily bind to whatever loop is running
            self._sem = asyncio.Semaphore(self._enrich_max)
        return self._sem

    def enqueue_enrich(self, workspace, agent_id: str, res) -> None:
        """Schedule the EXPENSIVE (vision/LLM) description as an INDEPENDENT background task —
        fire-and-forget, bounded concurrency, deduped per file-version. Returns immediately,
        so resource creation/listing never waits on understanding (OpenClaw's media-pass model).
        No-op without a rich_fn (nothing expensive to do) or outside an event loop."""
        if self._rich_fn is None:
            return
        if getattr(res, "enriched", False):  # PERSISTED: already enriched -> never re-run (survives restart)
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        key = (agent_id, res.rel_path, res.sig)
        if key in self._enriched:            # in-flight this session
            return
        self._enriched.add(key)
        task = asyncio.create_task(self._enrich(workspace, agent_id, res.rel_path))
        self._enrich_tasks.add(task)
        task.add_done_callback(self._enrich_tasks.discard)

    async def _enrich(self, workspace, agent_id: str, rel_path: str) -> None:
        async with self._semaphore():        # cap concurrent vision/LLM calls
            try:
                await self.describe_rich(workspace, agent_id, rel_path)
            except Exception:  # noqa: BLE001 — background enrichment never breaks a run
                pass

    # ---- explicit rich (vision/LLM) describe (async seam) -------------------

    async def describe_rich(self, workspace, agent_id: str, rel_path: str) -> str:
        p = self._safe(workspace, rel_path)
        kind = kind_for_path(p)
        try:
            with p.open("rb") as f:
                sample = f.read(_SAMPLE_BYTES if kind != KIND_IMAGE else 2_000_000)
        except OSError:
            return ""
        desc = ""
        if self._rich_fn is not None:
            desc = await self._rich_fn(kind, p, sample)
        rich = bool(desc)                                          # the expensive describer succeeded
        if not desc:                                               # rich declined / failed -> basic
            desc = self._describer.describe(kind, p, sample)
        st = p.stat()
        # enriched=True only on a real rich result -> persisted, never re-run on restart;
        # a transient failure (rich="") stays False so it can retry later.
        self._store.put(agent_id, Resource(
            os.path.relpath(str(p), str(Path(workspace).resolve())).replace("\\", "/"),
            kind, st.st_size, _sig(st.st_size, st.st_mtime), desc, time.time(), enriched=rich))
        return desc

"""Workspace scratch + cleanup — a sanctioned throwaway area and an on-demand tidy.

`<workspace>/tmp/` is the SCRATCH dir: agents write intermediate/throwaway files there. It is
NOT indexed or enriched (skip-listed in both workspace indexes) and is auto-swept by age, so
scratch never clutters the resource manifest or gets summarized. `cleanup()` removes scratch —
the whole `tmp/` tree plus any file matching an explicit glob pattern — with a dry-run preview.

Everything here is bounded to the agent's own workspace and refuses the home directory, so it
can never wander into the user's machine.
"""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path

SCRATCH_DIRNAME = "tmp"  # <workspace>/tmp/ — the sanctioned scratch area


def _is_home(p: Path) -> bool:
    try:
        return p.resolve() == Path.home().resolve()
    except (OSError, RuntimeError):
        return False


def scratch_dir(workspace) -> Path:
    return Path(workspace) / SCRATCH_DIRNAME


def sweep_scratch(workspace, ttl_hours: float, *, now: float | None = None) -> int:
    """Delete files under `<workspace>/tmp/` older than `ttl_hours` (>0). Bounded strictly to
    the scratch dir — never touches durable workspace files. Returns the count deleted. A
    no-op when ttl_hours<=0, the dir is missing, or the workspace is home."""
    root = Path(workspace)
    if ttl_hours <= 0 or _is_home(root):
        return 0
    d = scratch_dir(root)
    if not d.is_dir():
        return 0
    import time

    cutoff = (now if now is not None else time.time()) - ttl_hours * 3600
    n = 0
    for p in d.rglob("*"):
        if not p.is_file():
            continue
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
                n += 1
        except OSError:
            pass
    return n


def plan_cleanup(workspace, *, patterns=()) -> list[str]:
    """The workspace-relative files `cleanup()` WOULD delete: EVERY file under `tmp/`, plus any
    file (outside tmp/) whose name matches a glob in `patterns` (e.g. 'tmp_*', '*.bak'). Read
    only — a dry run. Empty for the home directory (never tidied)."""
    root = Path(workspace)
    if _is_home(root):
        return []
    out: list[str] = []
    scratch = scratch_dir(root)
    if scratch.is_dir():
        for p in scratch.rglob("*"):
            if p.is_file():
                out.append(os.path.relpath(str(p), str(root)).replace("\\", "/"))
    if patterns:
        for p in root.rglob("*"):
            if not p.is_file() or scratch in p.parents or p.parent == scratch:
                continue
            if any(fnmatch.fnmatch(p.name, pat) for pat in patterns):
                out.append(os.path.relpath(str(p), str(root)).replace("\\", "/"))
    return sorted(set(out))


def cleanup(workspace, *, patterns=(), dry_run=False) -> list[str]:
    """Delete scratch: all of `tmp/` + files matching `patterns`. Returns the deleted (or, if
    dry_run, the would-delete) workspace-relative paths."""
    targets = plan_cleanup(workspace, patterns=patterns)
    if not dry_run:
        root = Path(workspace)
        for rel in targets:
            try:
                (root / rel).unlink()
            except OSError:
                pass
    return targets

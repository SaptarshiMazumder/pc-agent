"""Projects — named groups of chat sessions (ChatGPT/LM-Studio style folders).

A project is pure conversation ORGANIZATION, owned by the server so every client
(terminal, desktop, mobile) sees the same list: one JSON file in the daemon's root
state dir (projects are global — any agent's chat can live in any project). The
LINK lives on each session's meta sidecar (``projectId``), written by the gateway
when a chat is started inside a project — so a session is standalone by default
and joining/leaving a project never touches the append-only transcript.

Storage: ``<state_dir>/projects.json`` — ``{"projects": [{id, name, createdAt}]}``,
atomic replace on every write, insertion order preserved (oldest first).
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

MAX_PROJECT_NAME = 60


def _path(state_dir: Path) -> Path:
    return Path(state_dir) / "projects.json"


def _read(state_dir: Path) -> list[dict]:
    try:
        data = json.loads(_path(state_dir).read_text(encoding="utf-8"))
        return [p for p in data.get("projects", []) if isinstance(p, dict) and p.get("id")]
    except (OSError, ValueError):
        return []


def _write(state_dir: Path, projects: list[dict]) -> None:
    path = _path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"projects": projects}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(path)


def clean_project_name(raw: str) -> str:
    return " ".join((raw or "").split())[:MAX_PROJECT_NAME].strip()


def list_projects(state_dir: Path) -> list[dict]:
    """All projects, oldest first: [{id, name, createdAt}]. Never raises."""
    return _read(state_dir)


def get_project(state_dir: Path, project_id: str) -> dict | None:
    return next((p for p in _read(state_dir) if p["id"] == project_id), None)


def create_project(state_dir: Path, name: str) -> dict:
    """Create a project (name required). Returns the new record."""
    name = clean_project_name(name)
    if not name:
        raise ValueError("project name required")
    project = {"id": f"proj-{uuid.uuid4().hex[:8]}", "name": name, "createdAt": time.time()}
    _write(state_dir, _read(state_dir) + [project])
    return project


def rename_project(state_dir: Path, project_id: str, name: str) -> bool:
    """Rename; False if the project doesn't exist (or the new name is empty)."""
    name = clean_project_name(name)
    if not name:
        return False
    projects = _read(state_dir)
    for p in projects:
        if p["id"] == project_id:
            p["name"] = name
            _write(state_dir, projects)
            return True
    return False


def delete_project(state_dir: Path, project_id: str) -> bool:
    """Remove a project from the list. The caller decides what happens to its
    sessions (keep as standalone, or delete) — that's session-store territory."""
    projects = _read(state_dir)
    kept = [p for p in projects if p["id"] != project_id]
    if len(kept) == len(projects):
        return False
    _write(state_dir, kept)
    return True

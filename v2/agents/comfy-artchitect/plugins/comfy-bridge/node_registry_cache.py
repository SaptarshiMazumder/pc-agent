"""NodeRegistryCache — a box's node catalogue, kept per ComfyUI version, so a design can be checked
with no box.

A ComfyUI's `/api/object_info` is fixed by its VERSION plus the node packs installed. Captured once
from a real box, it answers every structural question about a workflow for any other box of that
version — which is all of them: the platform rents one pinned image. So the catalogue is saved
whenever a box is probed, and read back when there is none.

WHERE: `.studio/node_registry/<version>.json` in the account's workspace (written by the tool that
probed, read by the tools that validate). The plugin ships a FALLBACK for the pinned image's
version, extracted from ComfyUI's source: right for every native node, blind to options computed
at import time (those are marked `unchecked`) and to packs. The first live capture replaces it.

A CAPTURE FROM A BOX WITH PACKS lists the packs' nodes too. That is the truth about that box and
the next one of its version is likely to have them as well; a node that turns out missing on the
next box is caught by the live check before anything runs.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

SNAPSHOT_GLOB = "node_registry_snapshot_v*.json"
_FOLDER = (".studio", "node_registry")
_VERSION = re.compile(r"^v?(\d+(?:\.\d+){0,3})")


class NodeRegistryCache:
    def __init__(self, workspace: Path, shipped_dir: Path) -> None:
        self._dir = Path(workspace).joinpath(*_FOLDER)
        self._shipped_dir = Path(shipped_dir)

    @staticmethod
    def version_key(version: str) -> str:
        """'v0.35.0' / '0.35.0 (abc123)' -> '0.35.0'; '' when it is not a version."""
        m = _VERSION.match(str(version or "").strip())
        return m.group(1) if m else ""

    def save(self, version: str, object_info: dict) -> Path | None:
        """Keep a live box's catalogue. Returns the file, or None when the version is unknown or
        the catalogue is empty (nothing worth keeping)."""
        key = self.version_key(version)
        if not key or not object_info:
            return None
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{key}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"comfyui_version": key, "source": "live /api/object_info",
                                   "object_info": object_info}, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)
        return path

    def has_capture(self, version: str) -> bool:
        key = self.version_key(version)
        return bool(key) and (self._dir / f"{key}.json").is_file()

    def load(self, version: str = "") -> tuple[dict, str, str]:
        """(object_info, where it came from, the ComfyUI version it lists). For `version`: its live
        capture if there is one, else the shipped snapshot of that version; with no version, the
        newest live capture, else the shipped snapshot. Raises FileNotFoundError when nothing exists
        at all."""
        key = self.version_key(version)
        captured = self._read(self._dir / f"{key}.json") if key else self._newest_capture()
        if captured:
            return (captured["object_info"], f"captured from a ComfyUI {captured['comfyui_version']} box",
                    str(captured["comfyui_version"]))
        shipped = self._shipped(key)
        if shipped is None:
            raise FileNotFoundError(f"no node catalogue for ComfyUI {key or '(unknown)'}: none captured, none shipped")
        note = f"ComfyUI {shipped['comfyui_version']} node list shipped with the agent"
        if key and key != shipped["comfyui_version"]:
            note += f" (the box runs {key}; differences are caught on the box)"
        return shipped["object_info"], note, str(shipped["comfyui_version"])

    # ------------------------------------------------------------------ files

    def _newest_capture(self) -> dict | None:
        if not self._dir.is_dir():
            return None
        files = sorted(self._dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        return self._read(files[0]) if files else None

    def _shipped(self, key: str) -> dict | None:
        files = sorted(self._shipped_dir.glob(SNAPSHOT_GLOB))
        exact = [p for p in files if p.stem.endswith(f"v{key}")] if key else []
        return self._read((exact or files[-1:] or [None])[0]) if files else None

    @staticmethod
    def _read(path: Path | None) -> dict | None:
        if path is None or not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("object_info"), dict):
            raise ValueError(f"{path} is not a node catalogue")
        return data


__all__ = ["NodeRegistryCache"]

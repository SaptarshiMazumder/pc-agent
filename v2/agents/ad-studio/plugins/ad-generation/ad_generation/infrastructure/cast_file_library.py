"""The cast as folders in the workspace: cast/<name>/member.json + its character sheets.

The workspace, not a campaign: a cast member outlives every campaign they appear in.
"""

from __future__ import annotations

import json
import re

from ad_generation.domain.cast_member import CastMember
from ad_generation.infrastructure.run_workspace import RunWorkspace

ROOT = "cast"
_NAME = re.compile(r"^[a-z][a-z0-9-]{0,31}$")


class CastFileLibrary:
    def __init__(self, workspace: RunWorkspace) -> None:
        self._ws = workspace

    def all(self) -> list[CastMember]:
        root = self._ws.path(ROOT)
        if not root.is_dir():
            return []
        return [
            CastMember.from_dict(json.loads(p.read_text(encoding="utf-8")))
            for p in sorted(root.glob("*/member.json"))
        ]

    def get(self, name: str) -> CastMember:
        path = self._ws.path(f"{ROOT}/{name}/member.json")
        if not path.is_file():
            known = ", ".join(m.name for m in self.all()) or "none yet (cast_create makes one)"
            raise KeyError(f"no cast member '{name}' (cast: {known})")
        return CastMember.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def sheet_stem(self, name: str) -> str:
        if not _NAME.match(name):
            raise ValueError(f"'{name}' is not a usable name: lowercase letters, digits and '-', starting with a letter")
        folder = self._ws.path(f"{ROOT}/{name}")
        folder.mkdir(parents=True, exist_ok=True)
        n = 1
        while any(folder.glob(f"sheet-{n:02d}*")):
            n += 1
        return f"{ROOT}/{name}/sheet-{n:02d}"

    def save(self, member: CastMember) -> None:
        path = self._ws.path(f"{ROOT}/{member.name}/member.json")
        path.write_text(json.dumps(member.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

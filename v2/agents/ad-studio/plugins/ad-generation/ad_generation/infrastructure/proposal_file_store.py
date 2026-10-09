"""ProposalStore as one JSON file in the workspace (cast/proposals.json for cast members)."""

from __future__ import annotations

import json

from ad_generation.infrastructure.run_workspace import RunWorkspace


class ProposalFileStore:
    def __init__(self, workspace: RunWorkspace, file: str) -> None:
        self._ws = workspace
        self._file = file

    def _read(self) -> dict:
        path = self._ws.path(self._file)
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    def _write(self, key: str, value) -> None:
        data = {**self._read(), key: value}
        path = self._ws.path(self._file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def proposals(self) -> dict[str, dict]:
        return dict(self._read().get("proposals") or {})

    def save_proposals(self, proposals: dict[str, dict]) -> None:
        self._write("proposals", proposals)

    def approvals(self) -> list[dict]:
        return list(self._read().get("approvals") or [])

    def save_approvals(self, approvals: list[dict]) -> None:
        self._write("approvals", approvals)

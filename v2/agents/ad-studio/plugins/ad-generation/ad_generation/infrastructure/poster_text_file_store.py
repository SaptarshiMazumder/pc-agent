"""PosterTextStore as files beside the design: <stem>.base.png (the clean picture) and
<stem>.text.json (the layers); an editor's preview is <stem>.edit.png."""

from __future__ import annotations

import json

from ad_generation.domain.poster_text import PosterText
from ad_generation.infrastructure.run_workspace import RunWorkspace


class PosterTextFileStore:
    def __init__(self, workspace: RunWorkspace) -> None:
        self._ws = workspace

    @staticmethod
    def _stem(design: str) -> str:
        return design.rsplit(".", 1)[0]

    def keep_base(self, design: str) -> str:
        base = self._stem(design) + ".base.png"
        self._ws.path(design).replace(self._ws.path(base))
        return base

    def save(self, design: str, text: PosterText) -> None:
        self._ws.path(self._stem(design) + ".text.json").write_text(
            json.dumps(text.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def load(self, design: str) -> PosterText | None:
        path = self._ws.path(self._stem(design) + ".text.json")
        return PosterText.from_dict(json.loads(path.read_text(encoding="utf-8"))) if path.is_file() else None

    def edit_path(self, design: str) -> str:
        return self._stem(design) + ".edit.png"

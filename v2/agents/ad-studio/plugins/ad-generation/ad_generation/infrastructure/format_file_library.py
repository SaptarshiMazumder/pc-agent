"""Ad formats as markdown files shipped with the plugin: formats/<key>.md, first line `# Title`."""

from __future__ import annotations

from pathlib import Path

from ad_generation.domain.ad_format import AdFormat


class FormatFileLibrary:
    def __init__(self, folder: Path) -> None:
        self._folder = folder

    def all(self) -> list[AdFormat]:
        return [self._load(p) for p in sorted(self._folder.glob("*.md"))]

    def get(self, key: str) -> AdFormat:
        path = self._folder / f"{key}.md"
        if not path.is_file():
            known = ", ".join(p.stem for p in sorted(self._folder.glob("*.md")))
            raise KeyError(f"no ad format '{key}' (formats: {known})")
        return self._load(path)

    @staticmethod
    def _load(path: Path) -> AdFormat:
        text = path.read_text(encoding="utf-8")
        first = text.splitlines()[0] if text else ""
        return AdFormat(key=path.stem, title=first.lstrip("# ").strip() or path.stem, guide=text)

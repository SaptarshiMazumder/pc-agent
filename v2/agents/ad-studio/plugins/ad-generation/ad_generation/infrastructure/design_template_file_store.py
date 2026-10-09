"""DesignTemplateStore as files: built-ins in the plugin (design_templates/<slug>/template.html,
meta.json, thumb.jpg — read-only), the user's own in the workspace (posts/templates/<slug>/...).
A built-in's preview is copied into the workspace on first use so the window can show it."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from ad_generation.domain.design_template import DesignTemplate
from ad_generation.infrastructure.collection_file_store import slugify
from ad_generation.infrastructure.run_workspace import RunWorkspace

SAVED = "posts/templates"
BUILTIN_PREVIEWS = "posts/templates/.builtin"


class DesignTemplateFileStore:
    def __init__(self, workspace: RunWorkspace, builtin: Path) -> None:
        self._ws = workspace
        self._builtin = builtin

    def all(self) -> list[DesignTemplate]:
        saved = []
        root = self._ws.path(SAVED)
        if root.is_dir():
            found = [(json.loads(p.read_text(encoding="utf-8")), p) for p in root.glob("*/meta.json") if not p.parent.name.startswith(".")]
            saved = [DesignTemplate.from_dict(d) for d, _ in sorted(found, key=lambda t: t[0].get("created", 0), reverse=True)]
        return saved + [self._builtin_template(p.parent) for p in sorted(self._builtin.glob("*/meta.json")) if not p.parent.name.startswith("_")]

    def get(self, slug: str) -> DesignTemplate:
        for t in self.all():
            if t.slug == slug:
                return t
        raise KeyError(f"no template '{slug}' (templates: {', '.join(t.slug for t in self.all())})")

    def html(self, template: DesignTemplate) -> str:
        path = self._builtin / template.slug / "template.html" if template.origin == "builtin" else self._ws.path(template.html)
        return Path(path).read_text(encoding="utf-8")

    def save(self, name: str, description: str, kind: str, tags: list[str], html_path: str, preview_path: str) -> DesignTemplate:
        base, n = slugify(name), 2
        slug = base
        while self._ws.path(f"{SAVED}/{slug}").exists() or (self._builtin / slug).exists():
            slug, n = f"{base}-{n}", n + 1
        folder = self._ws.path(f"{SAVED}/{slug}")
        folder.mkdir(parents=True)
        shutil.copyfile(self._ws.path(html_path), folder / "template.html")
        thumb = ""
        if preview_path:
            shutil.copyfile(self._ws.path(preview_path), folder / "thumb.jpg")
            thumb = f"{SAVED}/{slug}/thumb.jpg"
        template = DesignTemplate(slug=slug, name=name.strip(), description=description.strip(), kind=kind,
                                  html=f"{SAVED}/{slug}/template.html", thumb=thumb, tags=tuple(tags), origin="saved")
        (folder / "meta.json").write_text(json.dumps({**template.to_dict(), "created": time.time()}, indent=2, ensure_ascii=False), encoding="utf-8")
        return template

    def delete(self, slug: str) -> None:
        folder = self._ws.path(f"{SAVED}/{slug}")
        if not (folder / "meta.json").is_file():
            raise ValueError(f"'{slug}' is a built-in template or does not exist — only your own can be deleted")
        shutil.rmtree(folder)

    def _builtin_template(self, folder: Path) -> DesignTemplate:
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        thumb = f"{BUILTIN_PREVIEWS}/{folder.name}.jpg"
        target = self._ws.path(thumb)
        if not target.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(folder / "thumb.jpg", target)
        return DesignTemplate(slug=folder.name, name=meta["name"], description=meta["description"], kind=meta["kind"],
                              html=f"design_templates/{folder.name}/template.html", thumb=thumb, tags=tuple(meta.get("tags") or ()))

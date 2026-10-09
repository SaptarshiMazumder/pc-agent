"""DesignReferenceStore as files in the workspace: posts/references/<slug>/{image.jpg,
reference.json}. Pictures are kept as JPEG, cropped with Pillow."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from PIL import Image

from ad_generation.domain.design_reference import DesignReference
from ad_generation.infrastructure.collection_file_store import slugify
from ad_generation.infrastructure.run_workspace import RunWorkspace

ROOT = "posts/references"
_MIN_SIDE = 120  # a crop smaller than this is a mistake, not a design


class DesignReferenceFileStore:
    def __init__(self, workspace: RunWorkspace) -> None:
        self._ws = workspace

    def all(self) -> list[DesignReference]:
        root = self._ws.path(ROOT)
        if not root.is_dir():
            return []
        found = [DesignReference.from_dict(json.loads(p.read_text(encoding="utf-8"))) for p in root.glob("*/reference.json")]
        return sorted(found, key=lambda r: r.created, reverse=True)

    def get(self, slug: str) -> DesignReference:
        path = self._ws.path(f"{ROOT}/{slug}/reference.json")
        if not path.is_file():
            raise KeyError(f"no reference '{slug}' (references: {', '.join(r.slug for r in self.all()) or 'none yet'})")
        return DesignReference.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def new_slug(self, name: str) -> str:
        base, n = slugify(name), 2
        slug = base
        while self._ws.path(f"{ROOT}/{slug}").exists():
            slug, n = f"{base}-{n}", n + 1
        return slug

    def import_image(self, slug: str, src: str, crop: tuple[int, int, int, int] | None) -> str:
        source = self._ws.path(src)
        try:
            self._ws.rel(source)
        except ValueError:
            raise ValueError(f"{src} is outside the workspace — save the screenshot into it first (browser screenshot with `to`)") from None
        if not source.is_file():
            raise ValueError(f"no file {src}")
        folder = self._ws.path(f"{ROOT}/{slug}")
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / "image.jpg"
        with Image.open(source) as im:
            im = im.convert("RGB")
            if crop:
                x, y, w, h = crop
                if x < 0 or y < 0 or w < _MIN_SIDE or h < _MIN_SIDE or x + w > im.width or y + h > im.height:
                    shutil.rmtree(folder)
                    raise ValueError(f"crop {crop} is not inside the {im.width}×{im.height} picture (or is under {_MIN_SIDE}px)")
                im = im.crop((x, y, x + w, y + h))
            im.save(target, "JPEG", quality=90)
        return f"{ROOT}/{slug}/image.jpg"

    def fingerprint(self, image: str) -> str:
        return hashlib.sha256(self._ws.path(image).read_bytes()).hexdigest()

    def save(self, reference: DesignReference) -> None:
        folder = self._ws.path(f"{ROOT}/{reference.slug}")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "reference.json").write_text(json.dumps(reference.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    def discard(self, slug: str) -> None:
        shutil.rmtree(self._ws.path(f"{ROOT}/{slug}"), ignore_errors=True)

    def delete(self, slug: str) -> None:
        folder: Path = self._ws.path(f"{ROOT}/{slug}")
        if not (folder / "reference.json").is_file():
            raise KeyError(f"no reference '{slug}'")
        shutil.rmtree(folder)

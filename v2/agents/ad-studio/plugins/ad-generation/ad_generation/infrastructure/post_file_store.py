"""PostStore as files: posts/<slug>/post.json, the render in posts/<slug>/out/, and beside it
caption.txt and <slug>.zip (every file in order, and the caption)."""

from __future__ import annotations

import hashlib
import json
import shutil
import time
import zipfile
from pathlib import Path

from ad_generation.domain.design_progress import DesignProgress
from ad_generation.domain.post import Post
from ad_generation.infrastructure.collection_file_store import slugify
from ad_generation.infrastructure.run_workspace import RunWorkspace

ROOT = "posts"
_SWAP_TRIES = 20
_SWAP_WAIT_S = 0.05
_POST_MEDIA = (".png", ".jpg", ".jpeg", ".mp4")


class PostFileStore:
    def __init__(self, workspace: RunWorkspace) -> None:
        self._ws = workspace

    def all(self) -> list[Post]:
        root = self._ws.path(ROOT)
        if not root.is_dir():
            return []
        found = [
            (p.stat().st_mtime, Post.from_dict(json.loads(p.read_text(encoding="utf-8"))))
            for p in root.glob("*/post.json")
            if not p.parent.name.startswith(".")
        ]
        return [post for _, post in sorted(found, key=lambda t: t[0], reverse=True)]

    def get(self, slug: str) -> Post:
        path = self._ws.path(f"{ROOT}/{slug}/post.json")
        if not path.is_file():
            known = ", ".join(p.slug for p in self.all()) or "none yet"
            raise KeyError(f"no post '{slug}' (posts: {known})")
        return Post.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def new_slug(self, name: str) -> str:
        base = slugify(name)
        slug, n = base, 2
        while self._ws.path(f"{ROOT}/{slug}").exists():
            slug, n = f"{base}-{n}", n + 1
        return slug

    def save(self, post: Post) -> None:
        path = self._ws.path(f"{ROOT}/{post.slug}/post.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(post.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    def out_dir(self, slug: str) -> str:
        rel = f"{ROOT}/{slug}/out"
        folder = self._ws.path(rel)
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True)
        return rel

    def stage_files(self, slug: str, files: list[str]) -> list[str]:
        out = f"{ROOT}/{slug}/staged"
        folder = self._ws.path(out)
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True)
        pages: list[tuple[str, bytes]] = []
        for f in files:
            src = self._ws.path(f)
            if not src.is_file():
                raise ValueError(f"no file {f}")
            if src.suffix.lower() == ".zip":
                with zipfile.ZipFile(src) as z:
                    names = sorted(n for n in z.namelist() if n.lower().endswith(_POST_MEDIA))
                    pages += [(Path(n).suffix.lower(), z.read(n)) for n in names]
            elif src.suffix.lower() in _POST_MEDIA:
                pages.append((src.suffix.lower(), src.read_bytes()))
            else:
                raise ValueError(f"{f}: Instagram takes png, jpg or mp4 — not {src.suffix}")
        if not pages:
            raise ValueError("none of those files holds a png, jpg or mp4")
        made = []
        for n, (suffix, data) in enumerate(pages, 1):
            rel = f"{out}/{n:02d}{'.jpg' if suffix == '.jpeg' else suffix}"
            self._ws.path(rel).write_bytes(data)
            made.append(rel)
        return made

    def same_as_render(self, slug: str, staged: list[str]) -> bool:
        out = self._ws.path(f"{ROOT}/{slug}/out")
        if not out.is_dir():
            return False
        rendered = {hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir() if p.is_file()}
        return any(hashlib.sha256(self._ws.path(s).read_bytes()).hexdigest() in rendered for s in staged)

    def promote(self, slug: str) -> list[str]:
        staged = self._ws.path(f"{ROOT}/{slug}/staged")
        if not staged.is_dir():
            raise ValueError(f"post {slug} has nothing staged")
        out = self.out_dir(slug)
        folder = self._ws.path(out)
        folder.rmdir()
        staged.rename(folder)
        return [f"{out}/{p.name}" for p in sorted(folder.iterdir())]

    def discard(self, slug: str) -> None:
        staged = self._ws.path(f"{ROOT}/{slug}/staged")
        if staged.exists():
            shutil.rmtree(staged)

    def write_design(self, slug: str, n: int, html: str) -> str:
        rel = f"{ROOT}/{slug}/designs/{n:02d}.html"
        path = self._ws.path(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
        return rel

    def save_design_progress(self, progress: DesignProgress) -> None:
        path = self._ws.path(f"{ROOT}/{progress.post}/designs/progress.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        # Written whole, then swapped in: the window reads it while the slides write it. On Windows
        # the swap is refused while a reader (the window, OneDrive's sync) holds the file open for a
        # moment — so it is tried again briefly, and only a lock that lasts is an error.
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(progress.to_dict(), ensure_ascii=False), encoding="utf-8")
        for attempt in range(_SWAP_TRIES):
            try:
                tmp.replace(path)
                return
            except PermissionError:
                if attempt == _SWAP_TRIES - 1:
                    raise
                time.sleep(_SWAP_WAIT_S)

    def design_progress(self, slug: str) -> DesignProgress | None:
        path = self._ws.path(f"{ROOT}/{slug}/designs/progress.json")
        return DesignProgress.from_dict(json.loads(path.read_text(encoding="utf-8"))) if path.is_file() else None

    def design_preview(self, slug: str, n: int) -> str:
        return f"{ROOT}/{slug}/designs/{n:02d}.jpg"

    def scratch(self, name: str) -> str:
        rel = f"{ROOT}/.work/{name}"
        self._ws.path(rel).mkdir(parents=True, exist_ok=True)
        return rel

    def bundle(self, slug: str, files: list[str], caption: str) -> tuple[str, str]:
        caption_rel, zip_rel = f"{ROOT}/{slug}/caption.txt", f"{ROOT}/{slug}/{slug}.zip"
        self._ws.path(caption_rel).write_text(caption, encoding="utf-8")
        with zipfile.ZipFile(self._ws.path(zip_rel), "w", zipfile.ZIP_DEFLATED) as z:
            for f in files:
                z.write(self._ws.path(f), f.rsplit("/", 1)[-1])
            z.write(self._ws.path(caption_rel), "caption.txt")
        return caption_rel, zip_rel

"""build_prebuilt_templates — compile every template into ``_prebuilt/<name>/``, if stale.

NOT ``_previews/``. Two prebuilt flavors exist and they must never be one directory:

  * ``_previews/<name>/``  — DISPLAY builds for the create dialog's thumbnail pane, built with a
    Gate-less entry so they show the app's layout instead of a login card. Cosmetic, never
    shipped into an agent.
  * ``_prebuilt/<name>/``  — THIS script's output: the template built EXACTLY as scaffolded,
    Gate and all. ``create_agent`` copies it in as a new agent's real ``ui/``. Copying the
    display flavor here would ship windows with sign-in bypassed.

Each template is the skeleton overlaid with ``_variants/<name>/``; the build here is byte-for-
byte what a new agent's first compile would produce, so ``create_agent`` copying it in as the
agent's ``ui/`` (ScaffoldReactAppService._install_prebuilt_ui) means creating an agent never
runs a build anywhere.

WHY IT RUNS FROM THE VENDOR PIPELINE (clients/sdk-js/scripts/vendor.mjs). A preview is a
snapshot; a snapshot that someone must remember to refresh is a snapshot that will be stale on
the one release it matters. The vendor script already runs on every SDK build — and previews
embed the vendored SDK — so previews refresh exactly when their inputs can have changed, with
nothing to remember. Manual runs work too:

    python build_prebuilt_templates.py [--force]

STALENESS IS DETECTED BY CONTENT, not by faith: a hash over every input file (skeleton, variant,
_common — the same sets ScaffoldReactAppService copies) is stamped into each build; matching
hash = skip in under a second, so the SDK's inner build loop stays fast.

The assembly here MIRRORS ScaffoldReactAppService._plan (skeleton minus SKIP_DIRS, variant
overlay minus README, _common into src/common). Mirrors, not imports: this script must run with
no daemon package on the path, on any dev box. If the two ever drift, the failure is soft and
self-announcing — the preview differs from the first real build — but keep them in step.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent
SKELETON = TEMPLATES / "_skeleton"
VARIANTS = TEMPLATES / "_variants"
COMMON = TEMPLATES / "_common"
PREBUILT = TEMPLATES / "_prebuilt"

#: Mirrors ScaffoldReactAppService.SKIP_DIRS — build output and installed packages are not the
#: template.
SKIP_DIRS = frozenset({"node_modules", "dist", "ui", "__pycache__", ".vite"})

STAMP_NAME = ".inputs-hash"


def template_names() -> list[str]:
    names = {"chat"}
    if VARIANTS.is_dir():
        names.update(d.name for d in VARIANTS.iterdir() if d.is_dir())
    return sorted(names)


def _input_files(template: str) -> list[Path]:
    roots = [SKELETON, COMMON]
    variant = VARIANTS / template
    if variant.is_dir():
        roots.append(variant)
    files: list[Path] = []
    for root in roots:
        for f in sorted(root.rglob("*")):
            if f.is_file() and not any(part in SKIP_DIRS for part in f.relative_to(root).parts):
                files.append(f)
    return files


def inputs_hash(template: str) -> str:
    h = hashlib.sha256()
    for f in _input_files(template):
        h.update(f.as_posix().encode())
        h.update(f.read_bytes())
    return h.hexdigest()


def assemble(template: str, app_dir: Path) -> None:
    """The scaffold's plan, materialized: skeleton, then the variant's overlay, then _common."""
    plan: dict[str, Path] = {}
    for src in sorted(SKELETON.rglob("*")):
        if src.is_file():
            rel = src.relative_to(SKELETON)
            if not any(part in SKIP_DIRS for part in rel.parts):
                plan[rel.as_posix()] = src
    variant = VARIANTS / template
    if variant.is_dir():
        for src in sorted(variant.rglob("*")):
            if src.is_file():
                rel = src.relative_to(variant)
                if any(part in SKIP_DIRS for part in rel.parts) or rel.name == "README.md":
                    continue
                plan[rel.as_posix()] = src
    for src in sorted(COMMON.rglob("*")):
        if src.is_file():
            plan[f"src/common/{src.relative_to(COMMON).as_posix()}"] = src

    for rel, src in plan.items():
        dest = app_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)


def _link_modules(app_dir: Path) -> None:
    """The skeleton's installed modules, without copying ~200 MB per template. Symlink first;
    on Windows without developer mode that needs a junction instead."""
    modules = SKELETON / "node_modules"
    if not modules.is_dir():
        raise SystemExit(
            f"no node_modules at {modules} — run `npm ci` in _skeleton once; previews build "
            f"with the same dependencies every agent gets."
        )
    target = app_dir / "node_modules"
    try:
        target.symlink_to(modules, target_is_directory=True)
        return
    except OSError:
        pass
    if os.name == "nt":
        r = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(target), str(modules)], capture_output=True
        )
        if r.returncode == 0:
            return
    raise SystemExit(f"could not link {target} -> {modules}; build previews on a box that can")


def build_one(template: str, force: bool) -> str:
    """'built' | 'current' — and raises/exits loudly on a broken template, because a template
    that cannot build means create_agent would hand out a window that cannot build either."""
    stamp = PREBUILT / template / STAMP_NAME
    digest = inputs_hash(template)
    if not force and stamp.is_file() and stamp.read_text(encoding="utf-8").strip() == digest:
        return "current"

    npm = shutil.which("npm")
    if not npm:
        raise SystemExit("npm is not on PATH — prebuilt templates need the toolchain the SDK uses")

    with tempfile.TemporaryDirectory(prefix=f"prebuilt-{template}-") as td:
        app_dir = Path(td) / "app"
        app_dir.mkdir()
        assemble(template, app_dir)
        _link_modules(app_dir)
        r = subprocess.run(
            [npm, "run", "build"],
            cwd=app_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",  # vite prints unicode; a cp932 console must not crash the capture
            timeout=600,
            env={**os.environ, "CI": "1", "NO_COLOR": "1"},
        )
        if r.returncode != 0:
            print(r.stdout or "", file=sys.stderr)
            print(r.stderr or "", file=sys.stderr)
            raise SystemExit(f"template '{template}' does not build — fix it before it ships")
        ui = Path(td) / "ui"
        if not (ui / "index.html").is_file():
            raise SystemExit(f"template '{template}' built but wrote no ui/index.html")

        dest = PREBUILT / template
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(ui, dest)
        (dest / STAMP_NAME).write_text(digest + "\n", encoding="utf-8")
    return "built"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true", help="rebuild even when inputs are unchanged")
    ap.add_argument("templates", nargs="*", help="specific template names (default: all)")
    args = ap.parse_args()

    wanted = args.templates or template_names()
    results = {}
    for name in wanted:
        results[name] = build_one(name, args.force)
        print(f"  prebuilt {name}: {results[name]}")
    print(json.dumps(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())

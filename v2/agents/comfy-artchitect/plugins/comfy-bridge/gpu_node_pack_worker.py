"""Install a custom node pack straight from its git repository, ON the GPU machine.

Stdlib only — shipped through the machine's portal provisioner (GpuCommandBundle), the same door
the model downloader uses. It exists for the packs ComfyUI-Manager's registry does not list, which
Manager will only install from a raw git URL at a security level we never lower.

What it does, exactly what Manager does for a git install: clone into ComfyUI/custom_nodes, pip
install the pack's requirements into ComfyUI's own Python, run its install.py if it has one, then
restart ComfyUI so the nodes load (at a pinned commit when the link ends in `@<commit>`). Only repositories on the
hosts below, only an owner/repo path —
the URL is checked here, on the machine, as well as by the plugin.

SEVERAL PACKS, ONE RESTART. A template names every pack it needs; installed one call at a time
each paid for its own ComfyUI restart (about a minute each). Given a list, the clones run at once
(each writes its own folder), the requirement installs run one after another (two pip runs on one
Python environment corrupt each other), and ComfyUI restarts once, after the last.

Progress is a small JSON file in ComfyUI's temp folder (read over /api/view) - the whole job's
state and each pack's (`packs`: {folder: state}); the final answer is written after the restart,
because ComfyUI empties that folder when it starts.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

from comfy_process_control import ComfyProcessControl

HOSTS = ("github.com", "gitlab.com", "huggingface.co")
_PATH = re.compile(r"^/([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+?)(?:\.git)?/?$")
#: A pinned pack: `<repository link>@<commit>` — the commit a design was built against, when the
#: pack's latest renamed the inputs it uses.
_PIN = re.compile(r"^(.*?)@([0-9a-f]{7,40})$")
STATUS_DIR = "agentd-node-installs"


def pinned(url: str) -> tuple[str, str]:
    """(the link without its pin, the pinned commit or '')."""
    match = _PIN.match((url or "").strip())
    return (match.group(1), match.group(2)) if match else ((url or "").strip(), "")


def repository(url: str) -> tuple[str, str]:
    """(clone URL, folder name) for an allowed repository link — ValueError for anything else. A
    pinned link (`…@<commit>`) names the same repository and folder."""
    parts = urlsplit(pinned(url)[0])
    match = _PATH.match(parts.path or "")
    if parts.scheme != "https" or parts.hostname not in HOSTS or parts.port or parts.username or not match:
        raise ValueError(
            "a node pack installs from its repository page only: https://github.com/<owner>/<repo> "
            "(or gitlab.com / huggingface.co)"
        )
    owner, name = match.group(1), match.group(2)
    if name in (".", ".."):
        raise ValueError("not a repository name")
    return f"https://{parts.hostname}/{owner}/{name}.git", name


class GpuNodePackWorker:
    def __init__(self, root: Path, *, run=subprocess.run, control=None, python: str = "") -> None:
        self.root = root
        self._run = run
        self._control = control or ComfyProcessControl()
        # ComfyUI's own Python — Vast's image keeps it in /venv/main — so requirements land where
        # the nodes are imported, not in whatever python3 the provisioner happened to start.
        venv = Path(os.environ.get("VENV_DIR", "/venv/main")) / "bin" / "python"
        self._python = python or (str(venv) if venv.exists() else sys.executable)

    def run(self, urls, job: str) -> None:
        """Install one pack (a URL) or several (a list), then restart ComfyUI once."""
        packs: dict[str, str] = {}

        def report(state: str, **fields) -> None:
            folder = self.root / "temp" / STATUS_DIR
            folder.mkdir(parents=True, exist_ok=True)
            data = {"state": state, "updated_at": time.time(), "packs": dict(packs), **fields}
            (folder / f"{job}.json").write_text(json.dumps(data), encoding="utf-8")

        try:
            links = [urls] if isinstance(urls, str) else list(urls)
            targets = [repository(u) for u in links]
            commits = {repository(u)[1]: pinned(u)[1] for u in links}
            if not targets:
                raise ValueError("no node pack to install")
            for _, name in targets:
                packs[name] = "queued"

            def fetch(target) -> None:
                clone, name = target
                dest = self.root / "custom_nodes" / name
                commit = commits.get(name, "")
                if not dest.exists():
                    packs[name] = "cloning"
                    # A pin needs the history to reach its commit; the latest needs only the tip.
                    self._step(["git", "clone", *([] if commit else ["--depth", "1"]), clone, str(dest)], timeout=900)
                elif commit:
                    packs[name] = "fetching"  # already there, maybe at another version: bring the pin in
                    self._step(["git", "-C", str(dest), "fetch", "--unshallow"] if (dest / ".git" / "shallow").exists()
                               else ["git", "-C", str(dest), "fetch", "origin"], timeout=900)
                if commit:
                    self._step(["git", "-C", str(dest), "checkout", "--force", commit], timeout=120)

            report("cloning")
            with ThreadPoolExecutor(max_workers=min(8, len(targets))) as pool:
                for future in [pool.submit(fetch, t) for t in targets]:
                    future.result()
            for _, name in targets:
                dest = self.root / "custom_nodes" / name
                packs[name] = "installing"
                report("installing")
                if (dest / "requirements.txt").is_file():
                    self._step([self._python, "-m", "pip", "install", "-r", str(dest / "requirements.txt")],
                               timeout=1800)
                if (dest / "install.py").is_file():
                    self._step([self._python, "install.py"], timeout=1800, cwd=str(dest))
                packs[name] = "installed"
            report("restarting")
            self._control.restart()
            self._control.wait_ready()
            for name in packs:
                packs[name] = "done"
            names = [name for _, name in targets]
            report("done", folder=names[0], folders=names)
        except Exception as error:  # noqa: BLE001 — the answer the plugin reads
            for name, state in packs.items():
                if state not in ("installed", "queued"):
                    packs[name] = "failed"
            report("failed", error=f"{type(error).__name__}: {str(error)[:400]}")

    def _step(self, command: list[str], *, timeout: float, cwd: str | None = None) -> None:
        done = self._run(command, cwd=cwd, timeout=timeout, capture_output=True, text=True)
        if done.returncode != 0:
            tail = (done.stderr or done.stdout or "").strip()[-400:]
            raise RuntimeError(f"`{' '.join(command[:3])}` failed: {tail}")

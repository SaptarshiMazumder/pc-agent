"""Install a custom node pack straight from its git repository, ON the GPU machine.

Stdlib only — shipped through the machine's portal provisioner (GpuCommandBundle), the same door
the model downloader uses. It exists for the packs ComfyUI-Manager's registry does not list, which
Manager will only install from a raw git URL at a security level we never lower.

What it does, exactly what Manager does for a git install: clone into ComfyUI/custom_nodes, pip
install the pack's requirements into ComfyUI's own Python, run its install.py if it has one, then
restart ComfyUI so the nodes load. Only repositories on the hosts below, only an owner/repo path —
the URL is checked here, on the machine, as well as by the plugin.

Progress is a small JSON file in ComfyUI's temp folder (read over /api/view); the final answer is
written after the restart, because ComfyUI empties that folder when it starts.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from comfy_process_control import ComfyProcessControl

HOSTS = ("github.com", "gitlab.com", "huggingface.co")
_PATH = re.compile(r"^/([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+?)(?:\.git)?/?$")
STATUS_DIR = "agentd-node-installs"


def repository(url: str) -> tuple[str, str]:
    """(clone URL, folder name) for an allowed repository link — ValueError for anything else."""
    parts = urlsplit((url or "").strip())
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

    def run(self, url: str, job: str) -> None:
        def report(state: str, **fields) -> None:
            folder = self.root / "temp" / STATUS_DIR
            folder.mkdir(parents=True, exist_ok=True)
            data = {"state": state, "updated_at": time.time(), **fields}
            (folder / f"{job}.json").write_text(json.dumps(data), encoding="utf-8")

        try:
            clone, name = repository(url)
            dest = self.root / "custom_nodes" / name
            if dest.exists():
                report("installing", note="already on the machine; requirements re-checked")
            else:
                report("cloning")
                self._step(["git", "clone", "--depth", "1", clone, str(dest)], timeout=900)
            if (dest / "requirements.txt").is_file():
                report("installing")
                self._step([self._python, "-m", "pip", "install", "-r", str(dest / "requirements.txt")],
                           timeout=1800)
            if (dest / "install.py").is_file():
                self._step([self._python, "install.py"], timeout=1800, cwd=str(dest))
            report("restarting")
            self._control.restart()
            self._control.wait_ready()
            report("done", folder=name)
        except Exception as error:  # noqa: BLE001 — the answer the plugin reads
            report("failed", error=f"{type(error).__name__}: {str(error)[:400]}")

    def _step(self, command: list[str], *, timeout: float, cwd: str | None = None) -> None:
        done = self._run(command, cwd=cwd, timeout=timeout, capture_output=True, text=True)
        if done.returncode != 0:
            tail = (done.stderr or done.stdout or "").strip()[-400:]
            raise RuntimeError(f"`{' '.join(command[:3])}` failed: {tail}")

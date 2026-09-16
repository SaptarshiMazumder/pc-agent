"""Standalone installer program bundled into the exported script. No daemon dependencies."""

import argparse
import getpass
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from gpu_model_download_worker import GpuModelDownloadWorker
from installer_source_policy import InstallerSourcePolicy
from model_download_redirect_policy import ModelDownloadRedirectPolicy
from model_download_request import ModelDownloadRequest


class WorkflowInstallerRuntime:
    def __init__(self, root, *, opener=None, command=subprocess.run, sleep=time.sleep):
        self.root = Path(root).resolve()
        self.opener = opener or urllib.request.build_opener(ModelDownloadRedirectPolicy())
        self.command = command
        self.sleep = sleep

    def destination(self, relative):
        relative = InstallerSourcePolicy.relative_path(relative)
        path = self.root / relative
        if path.is_symlink() or not path.resolve().is_relative_to(self.root):
            raise ValueError("Destination escapes ComfyUI or is a symlink")
        return path

    @staticmethod
    def sha256(path):
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(4 * 1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def open_model(self, url):
        headers = ModelDownloadRequest.headers()
        try:
            return self.opener.open(urllib.request.Request(url, headers=headers), timeout=60)
        except urllib.error.HTTPError as error:
            host = urlsplit(url).hostname
            variable = {"huggingface.co": "HF_TOKEN", "civitai.com": "CIVITAI_TOKEN"}.get(host)
            if error.code not in (401, 403, 404) or not variable:
                raise
            token = os.environ.get(variable, "")
            if not token and sys.stdin.isatty():
                token = getpass.getpass(f"Download refused. Enter your own {variable} (hidden; not saved): ").strip()
            if not token:
                raise ValueError(f"Provider refused access. Set your own {variable} and accept any model licence.") from None
            # Only the original provider receives a token. Redirects always strip it.
            headers["Authorization"] = f"Bearer {token}"
            return self.opener.open(urllib.request.Request(url, headers=headers), timeout=60)

    def download(self, model):
        url = InstallerSourcePolicy.source_url(model["url"])
        relative = InstallerSourcePolicy.relative_path(model["destination"])
        if not relative.startswith("models/"):
            raise ValueError("Weights must be installed under models/")
        target = self.destination(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        receipt = self.destination(f".workflow-installer/{hashlib.sha256(relative.encode()).hexdigest()}.json")
        expected = model.get("sha256")
        if target.exists():
            recorded = json.loads(receipt.read_text()) if receipt.exists() else {}
            expected = expected or (recorded.get("sha256") if recorded.get("url") == url else None)
            if expected and self.sha256(target) == expected:
                print(f"Verified existing: {relative}")
                return
            raise ValueError(f"Existing file cannot be verified; left untouched: {relative}")
        # A unique partial file prevents interrupted or concurrent writers from being mistaken
        # for loadable models. The installer-level lock serializes this program's own runs.
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".installer-", suffix=".part", delete=False) as tmp:
            partial = Path(tmp.name)
        try:
            for attempt in range(3):
                try:
                    with self.open_model(url) as response, partial.open("wb") as output:
                        if "text/html" in str(response.headers.get("Content-Type", "")).lower():
                            raise ValueError("Source returned a login/web page instead of model weights")
                        size = response.headers.get("Content-Length")
                        size = int(size) if size is not None else None
                        available = shutil.disk_usage(target.parent).free - 256 * 1024 * 1024
                        if available <= 0 or (size is not None and (size <= 0 or size > available)):
                            raise ValueError("Empty file or insufficient disk space")
                        received, last_report = 0, time.monotonic()
                        while True:
                            block = response.read(4 * 1024 * 1024)
                            if not block:
                                break
                            received += len(block)
                            if received > available or (size is not None and received > size):
                                raise ValueError("Download exceeds expected size or available disk")
                            output.write(block)
                            if time.monotonic() - last_report >= 5:
                                print(f"Downloading {target.name}: {received // (1024 * 1024)} MB", flush=True)
                                last_report = time.monotonic()
                        if not received or (size is not None and received != size):
                            raise ValueError("Incomplete model download")
                    break
                except urllib.error.HTTPError as error:
                    if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                        raise
                except (OSError, urllib.error.URLError):
                    if attempt == 2:
                        raise
                self.sleep(2 ** attempt)
            if target.suffix.lower() in (".safetensors", ".sft"):
                GpuModelDownloadWorker.verify(partial)
            digest = self.sha256(partial)
            if expected and digest != expected:
                raise ValueError("Model SHA256 does not match the recorded publisher hash")
            # Publish without replacing a destination created by somebody else in the meantime.
            os.link(partial, target)  # same-volume, atomic, fails rather than overwriting
            receipt.parent.mkdir(parents=True, exist_ok=True)
            receipt.write_text(json.dumps({"url": url, "sha256": digest}), encoding="utf-8")
            print(f"Installed: {relative}")
        finally:
            partial.unlink(missing_ok=True)

    def install_pack(self, pack, constraints):
        repo = InstallerSourcePolicy.repository_url(pack["repository"])
        name = InstallerSourcePolicy.relative_path(pack["directory"])
        if "/" in name:
            raise ValueError("Node pack directory must be a basename")
        destination = self.destination(f"custom_nodes/{name}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            result = self.command(["git", "-C", str(destination), "remote", "get-url", "origin"],
                                  check=True, capture_output=True, text=True)
            if InstallerSourcePolicy.repository_url(result.stdout.strip()) != repo:
                raise ValueError(f"Existing node folder has a different source: {name}")
            print(f"Keeping existing node repository (no pull/reset): {name}")
        else:
            self.command(["git", "clone", "--depth", "1", "--", repo, str(destination)], check=True)
        requirements = destination / "requirements.txt"
        if requirements.exists():
            self.command([sys.executable, "-m", "pip", "install", "--constraint", str(constraints),
                          "-r", str(requirements)], check=True, cwd=str(destination))
        if (destination / "install.py").exists():
            print(f"MANUAL STEP: {name} contains install.py; review the pack README before running it.")

    def run(self, manifest, *, yes=False, dry_run=False):
        if manifest.get("schema_version") != 1:
            raise ValueError("Unsupported installer manifest")
        if not (self.root / "main.py").is_file() or not (self.root / "models").is_dir():
            raise ValueError("--comfy-dir must point to an existing ComfyUI checkout (main.py and models/)")
        print(f"ComfyUI: {self.root}\nPython: {sys.executable}")
        for note in manifest["notes"]:
            print(note)
        for model in manifest["models"]:
            print(f"MODEL: {model['destination']} <- {model['url']}")
        for pack in manifest["node_packs"]:
            print(f"NODE CODE: {pack['repository']}")
        if manifest["references"]:
            print("INPUTS TO SUPPLY YOURSELF: " + "; ".join(manifest["references"]))
        if manifest["paid_api_nodes"]:
            print("PAID API NODES: " + ", ".join(manifest["paid_api_nodes"]) +
                  ". Configure your own account; future renders may spend its credits.")
        if manifest["unresolved"]:
            raise ValueError("Installer is INCOMPLETE; no changes made. Unresolved: " + "; ".join(manifest["unresolved"]))
        if dry_run:
            print("Dry run only; no network requests or changes made.")
            return
        if not yes and input("Stop ComfyUI first. Install these files and third-party node code? [y/N] ").lower() != "y":
            print("Cancelled; nothing installed.")
            return
        lock = self.destination(".workflow-installer.lock")
        with lock.open("x"):
            pass
        try:
            # Pin every existing Python package, including torch: incompatible requirements
            # fail explicitly instead of silently upgrading the user's working environment.
            with tempfile.TemporaryDirectory(prefix="comfy-installer-") as temporary:
                constraints = Path(temporary) / "constraints.txt"
                constraints.write_text("\n".join(
                    f"{d.metadata['Name']}=={d.version}" for d in importlib.metadata.distributions()
                    if re.fullmatch(r"[A-Za-z0-9_.-]+", d.metadata.get("Name", ""))
                    and re.fullmatch(r"[A-Za-z0-9_.+!-]+", d.version)
                ), encoding="utf-8")
                for model in manifest["models"]:
                    self.download(model)
                for pack in manifest["node_packs"]:
                    self.install_pack(pack, constraints)
            print("Downloads and standard node requirements installed. Review manual steps, restart ComfyUI, import the workflow and validate it locally.")
        finally:
            lock.unlink()

    @classmethod
    def main(cls, manifest):
        parser = argparse.ArgumentParser(description="Install this workflow's dependencies, never render it. Use ComfyUI's Python.")
        parser.add_argument("--comfy-dir", required=True, type=Path)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--yes", action="store_true", help="Accept downloading files and installing third-party node code")
        args = parser.parse_args()
        try:
            cls(args.comfy_dir).run(manifest, yes=args.yes, dry_run=args.dry_run)
        except urllib.error.HTTPError as error:
            print(f"Download failed: HTTP {error.code} from {urlsplit(error.url).hostname}. No success claimed.", file=sys.stderr)
            return 1
        except (OSError, ValueError, subprocess.CalledProcessError, urllib.error.URLError) as error:
            # Do not print urllib exception strings, which can contain signed redirect URLs.
            message = str(error) if isinstance(error, ValueError) else type(error).__name__
            print(f"Install stopped: {message}. Previously completed steps may remain.", file=sys.stderr)
            return 1
        return 0

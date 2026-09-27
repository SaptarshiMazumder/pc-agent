"""Point ComfyUI at the volume, once, so models downloaded there persist AND load.

Stdlib only — shipped to the person's own Vast machine and run there (GpuCommandBundle).

ComfyUI reads extra folders from `extra_model_paths.yaml` at startup only. So on a machine whose
volume is not `/workspace` (ModelStorageDetector), this writes one block naming
`<volume>/ComfyUI/models` and restarts ComfyUI — ONCE: the block is compared first, and an
unchanged file restarts nothing. Everything already on the volume from an earlier machine is
then listed by ComfyUI too, so it is never downloaded twice.

The block is ours alone, between two marker lines; whatever else the file holds is kept.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from comfy_process_control import ComfyProcessControl
from model_download_request import ModelDownloadRequest
from model_storage_detector import VOLUME, ModelStorageDetector

_BEGIN = "# agentd-volume-begin"
_END = "# agentd-volume-end"


class ModelStorageSetup:
    def __init__(self, comfy_root: Path, detector: ModelStorageDetector, *, restart, wait_ready) -> None:
        self._root = comfy_root
        self._detector = detector
        self._restart = restart
        self._wait_ready = wait_ready

    @classmethod
    def for_machine(cls, comfy_root: Path) -> "ModelStorageSetup":
        """The real machine: supervisor restarts ComfyUI, and it is ready when it answers."""
        control = ComfyProcessControl()
        return cls(
            comfy_root, ModelStorageDetector(comfy_root.parent),
            restart=control.restart, wait_ready=control.wait_ready,
        )

    def ensure(self) -> dict:
        """{kind, path, models_dir, restarted}: where this machine's models go from now on."""
        storage = self._detector.detect()
        if storage["kind"] != VOLUME:
            return {**storage, "models_dir": str(self._root / "models"), "restarted": False}
        models = Path(storage["path"]) / "ComfyUI" / "models"
        for folder in sorted(set(ModelDownloadRequest.DIRECTORIES.values())):
            (models / folder).mkdir(parents=True, exist_ok=True)
        changed = self._write_paths(models)
        if changed:
            self._restart()
            self._wait_ready()
        return {**storage, "models_dir": str(models), "restarted": changed}

    def _write_paths(self, models: Path) -> bool:
        path = self._root / "extra_model_paths.yaml"
        current = path.read_text(encoding="utf-8") if path.is_file() else ""
        lines = [_BEGIN, "agentd_volume:", f"    base_path: {json.dumps(str(models))}"]
        lines += [f"    {f}: {f}" for f in sorted(set(ModelDownloadRequest.DIRECTORIES.values()))]
        block = "\n".join(lines + [_END]) + "\n"
        kept = re.sub(re.escape(_BEGIN) + r".*?" + re.escape(_END) + r"\n?", "", current, flags=re.S)
        updated = (kept.rstrip("\n") + "\n\n" if kept.strip() else "") + block
        if updated == current:
            return False
        path.write_text(updated, encoding="utf-8")
        return True

    def report(self, storage: dict, nonce: str) -> None:
        """Leave the answer where the plugin reads it: ComfyUI's temp folder, via /api/view."""
        folder = self._root / "temp" / "agentd-model-downloads"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"storage-{nonce}.json").write_text(json.dumps(storage), encoding="utf-8")


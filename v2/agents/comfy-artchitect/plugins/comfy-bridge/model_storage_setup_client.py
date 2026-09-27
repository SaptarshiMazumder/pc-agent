"""Run ModelStorageSetup on the person's own Vast machine, and read back where models will live.

Submitted through the machine's portal provisioner (the same door the model downloader uses);
the answer is a small JSON file ComfyUI serves from its temp folder. Pointing ComfyUI at a volume
restarts it once, so the answer can take a minute: a check that has not answered yet comes back
as `pending` with its nonce, and `read` picks it up later.
"""

from __future__ import annotations

import json
import time
import uuid
from urllib.parse import urlencode

from gpu_command_bundle import GpuCommandBundle

PENDING = "pending"

_MODULES = ("model_download_request", "model_storage_detector", "comfy_process_control", "model_storage_setup")
_ENTRY = (
    "setup=sys.modules['model_storage_setup'].ModelStorageSetup.for_machine(root)\n"
    "try:\n"
    " answer=setup.ensure()\n"
    "except Exception as error:\n"
    " answer={'error': type(error).__name__+': '+str(error)[:300]}\n"
    "setup.report(answer, data['nonce'])"
)


class ModelStorageSetupClient:
    def __init__(self, *, fetch, sleep=time.sleep, clock=time.monotonic) -> None:
        self._fetch = fetch
        self._sleep = sleep
        self._clock = clock

    def run(self, connection: dict, wait_s: float = 90.0) -> dict:
        """{kind, path, models_dir, restarted} — or {kind: pending, nonce} if not answered yet."""
        nonce = uuid.uuid4().hex
        manifest = {"version": 1, "post_commands": [GpuCommandBundle.command(_MODULES, _ENTRY, {"nonce": nonce})],
                    "on_failure": {"action": "continue", "max_retries": 0}}
        res = self._fetch(
            connection["portal_url"].rstrip("/") + "/capabilities/provision", method="POST",
            headers={"Authorization": connection["auth"]},
            json={"inline_yaml": json.dumps(manifest)}, timeout_s=30,
        )
        if not res.ok or (res.json() or {}).get("status") != "started":
            raise ValueError(f"the machine's portal did not start the storage check (HTTP {res.status})")
        deadline = self._clock() + wait_s
        while True:
            answer = self.read(connection, nonce)
            if answer is not None or self._clock() >= deadline:
                return answer if answer is not None else {"kind": PENDING, "nonce": nonce}
            self._sleep(5)

    def read(self, connection: dict, nonce: str) -> dict | None:
        """The check's answer, or None while it is still running (or ComfyUI is restarting)."""
        query = urlencode({"filename": f"storage-{nonce}.json", "type": "temp",
                           "subfolder": "agentd-model-downloads", "t": time.time_ns()})
        res = self._fetch(f"{connection['url'].rstrip('/')}/api/view?{query}",
                          headers={"Authorization": connection["auth"]}, timeout_s=15)
        if not res.ok:
            return None
        answer = res.json()
        if answer.get("error"):
            raise ValueError(f"the storage check failed on the machine: {answer['error']}")
        return answer

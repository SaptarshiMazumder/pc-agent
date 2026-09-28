"""Start GpuNodePackWorker on the machine through its portal, and follow it until ComfyUI is back.

For the node packs ComfyUI-Manager's registry does not list. Works wherever there is a portal —
the platform's rented GPU and the person's own Vast machine; a ComfyUI connected by its address
alone has none (comfy_bridge._portal_connection says so).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from urllib.parse import urlencode

from gpu_command_bundle import GpuCommandBundle
from gpu_node_pack_worker import STATUS_DIR

_MODULES = ("comfy_process_control", "gpu_node_pack_worker")
_ENTRY = "sys.modules['gpu_node_pack_worker'].GpuNodePackWorker(root).run(data['repos'], data['job'])"
_POLL_S = 5.0
_LEASE_EVERY_S = 90.0


class GpuNodePackInstallClient:
    def __init__(self, *, fetch, get, lease, sleep=asyncio.sleep, clock=time.monotonic) -> None:
        self._fetch = fetch
        self._get = get
        self._lease = lease
        self._sleep = sleep
        self._clock = clock

    async def install(self, portal: dict, repos, wait_s: float, abort=None, report=None,
                      on_packs=None) -> dict:
        """Install one repository or a list of them in ONE job (one ComfyUI restart). The worker's
        final status ({state: done, folder, folders}) - ValueError when it failed or ran out of
        time. `on_packs` gets each pack's state ({folder: state}) as it changes."""
        repos = [repos] if isinstance(repos, str) else list(repos)
        job = hashlib.sha256(f"{repos}:{time.time_ns()}".encode()).hexdigest()[:24]
        manifest = {"version": 1,
                    "post_commands": [GpuCommandBundle.command(_MODULES, _ENTRY, {"repos": repos, "job": job})],
                    "on_failure": {"action": "continue", "max_retries": 0}}
        res = self._fetch(portal["portal_url"].rstrip("/") + "/capabilities/provision", method="POST",
                          headers={"Authorization": portal["auth"]},
                          json={"inline_yaml": json.dumps(manifest)}, timeout_s=30)
        if not res.ok or (res.json() or {}).get("status") != "started":
            raise ValueError(f"the machine's portal did not start the install (HTTP {res.status})")
        deadline = self._clock() + wait_s
        next_lease = self._clock() + _LEASE_EVERY_S
        last = ""
        while self._clock() < deadline:
            if abort is not None and abort.is_set():
                raise ValueError("stopped waiting; the install may still finish on the machine")
            status = self._status(job)
            state = (status or {}).get("state", "")
            if on_packs and (status or {}).get("packs"):
                on_packs(status["packs"])
            if state == "done":
                return status
            if state == "failed":
                raise ValueError(status.get("error") or "the install failed on the machine")
            if state and state != last and report:
                report(f"node pack: {state}")
                last = state
            if self._clock() >= next_lease:
                self._lease()
                next_lease = self._clock() + _LEASE_EVERY_S
            await self._sleep(_POLL_S)
        raise ValueError("the install did not finish in time; it may still complete on the machine")

    def _status(self, job: str) -> dict | None:
        """The worker's progress, or None while there is none yet or ComfyUI is restarting."""
        query = urlencode({"filename": f"{job}.json", "type": "temp", "subfolder": STATUS_DIR,
                           "t": time.time_ns()})
        res = self._get(f"/api/view?{query}", timeout_s=15)
        if not res.ok:
            return None
        try:
            status = res.json()
        except ValueError:
            return None
        return status if isinstance(status, dict) else None

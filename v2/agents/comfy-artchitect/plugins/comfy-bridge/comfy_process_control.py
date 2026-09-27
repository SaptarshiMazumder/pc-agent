"""Restart ComfyUI on a Vast machine and wait until it answers again.

Stdlib only — shipped to the machine (GpuCommandBundle) by whatever needs ComfyUI to reload: the
storage setup (new model folders) and the node-pack installer (new nodes). Vast's ComfyUI image
runs it under supervisor as `comfyui`, on the port its COMFYUI_ARGS name (18188 by default).

ComfyUI empties its temp folder when it starts, so anything that reports through that folder
writes its final answer AFTER `wait_ready`.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
import urllib.request

_READY_WAIT_S = 240.0


class ComfyProcessControl:
    def restart(self) -> None:
        subprocess.run(["supervisorctl", "restart", "comfyui"], check=True, timeout=120)

    def wait_ready(self) -> None:
        match = re.search(r"--port\s+(\d+)", os.environ.get("COMFYUI_ARGS", ""))
        url = f"http://127.0.0.1:{match.group(1) if match else '18188'}/api/system_stats"
        deadline = time.monotonic() + _READY_WAIT_S
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=5) as response:
                    if response.status == 200:
                        return
            except OSError:
                pass
            time.sleep(3)
        raise RuntimeError("ComfyUI did not come back after its restart")

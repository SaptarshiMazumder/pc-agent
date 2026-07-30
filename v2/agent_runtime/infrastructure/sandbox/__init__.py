"""Sandbox adapters (S17). LocalSandbox = host execution (the default). Docker/SSH
isolation adapters plug in behind the same port later; exec/computer are not yet routed
through it (wiring is the integration step).
"""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)


class LocalSandbox:
    name = "local"

    async def run(
        self,
        command: str,
        cwd: str | None = None,
        env: dict | None = None,
        timeout: float | None = None,
    ) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            return 124, "(timed out)"
        return proc.returncode or 0, (out or b"").decode(errors="replace")


def build_sandbox(config):
    """The execution sandbox. Default = LocalSandbox (host = unchanged). A 'docker'/'ssh'
    value selects an isolating adapter later; only local is implemented for now."""
    kind = (getattr(config, "sandbox", "") or "").lower()
    if kind and kind != "local":
        log.warning("sandbox '%s' not implemented yet; using local", kind)
    return LocalSandbox()

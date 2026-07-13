"""Sandbox port — where a tool's shell/exec runs (S17, seam).

`LocalSandbox` (the default) runs on the host = today's behavior. A `DockerSandbox` /
`SshSandbox` can later ISOLATE execution behind this SAME port (exec/computer route through
it). Default = local, so behavior is unchanged; the port is the seam isolation plugs into.
"""

from __future__ import annotations

from typing import Protocol


class Sandbox(Protocol):
    name: str

    async def run(
        self,
        command: str,
        cwd: str | None = None,
        env: dict | None = None,
        timeout: float | None = None,
    ) -> tuple[int, str]:
        """Run a shell command; return (exit_code, combined_output)."""
        ...

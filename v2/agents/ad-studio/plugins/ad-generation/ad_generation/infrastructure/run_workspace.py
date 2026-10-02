"""The current run's workspace. Every path a service or tool names is RELATIVE to it — the form
the host's fetch and the window both resolve the same way."""

from __future__ import annotations

from pathlib import Path

from agent_runtime.application.run_context import current_workspace


class RunWorkspace:
    def root(self) -> Path:
        # Asked per call, never cached: the workspace belongs to the run, and one plugin
        # instance serves every run (and, hosted, every account).
        return Path(current_workspace(".") or ".").resolve()

    def path(self, rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else self.root() / p

    def rel(self, path: Path) -> str:
        return path.resolve().relative_to(self.root()).as_posix()

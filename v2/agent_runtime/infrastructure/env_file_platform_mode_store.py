"""EnvFilePlatformModeStore — the Local/Cloud choice, kept beside the credentials it governs.

WHY THE ``.env`` AND NOT ``agentd.config.json``. The config file is tracked in git; the .env is
not. A user pressing a button in a settings screen must not produce a diff in the repository, and
this value is per-machine state rather than anything worth committing. It also sits next to the
two credentials whose use it decides — the session token and the model-proxy key — which is where
someone debugging "why is this billing me" would look.

Not a secret, so unlike the token next door there is nothing here that must stay out of logs.
"""

from __future__ import annotations

from pathlib import Path

from agent_runtime.domain import platform_mode
from agent_runtime.infrastructure.env_file import EnvFile

MODE_KEY = "AGENTD_PLATFORM_MODE"


class EnvFilePlatformModeStore:
    """:param env_path: the ``.env`` beside agentd.config.json."""

    def __init__(self, env_path: Path):
        self._env = EnvFile(env_path)

    def read(self) -> str:
        return platform_mode.normalize(self._env.read(MODE_KEY))

    def write(self, mode: str) -> None:
        self._env.update({MODE_KEY: platform_mode.normalize(mode)})

"""EnvFileModelProxyBinder — switch platform-key billing on and off, live and durably.

Both halves in one place, because doing one without the other is a bug with a long fuse:

  * persist  ``AGENTD_MODEL_PROXY_KEY`` in the user .env, so the choice survives a restart
  * reconfigure the live proxy, so the very NEXT model call routes correctly

Persisting alone gives a daemon that claims Cloud mode until something restarts it. Reconfiguring
alone gives one that silently drops back to the user's own keys after a reboot — and the user's
own keys still work, so nothing looks broken; the bill just moves.

The URL is not written here. It comes from the build's distribution profile (or config/env) and is
resolved by ``model_proxy.configure``; this only supplies the credential that activates it.
"""

from __future__ import annotations

from pathlib import Path

from agent_runtime.infrastructure.env_file import EnvFile
from agent_runtime.infrastructure.llm import model_proxy

PROXY_KEY = "AGENTD_MODEL_PROXY_KEY"
#: Cleared alongside it so a stale pre-rename credential can never reactivate after a sign-out.
LEGACY_PROXY_KEY = "AGENTD_MODEL_GATEWAY_KEY"


class EnvFileModelProxyBinder:
    """:param config: the live Config — re-read by ``model_proxy.configure`` on every change."""

    def __init__(self, env_path: Path, config):
        self._env = EnvFile(env_path)
        self._config = config

    @property
    def available(self) -> bool:
        """Is a proxy URL configured? Asked of the seam rather than the config so this cannot
        disagree with what would actually happen on the next call."""
        return bool(model_proxy.status().get("api_base"))

    @property
    def bound(self) -> bool:
        return bool(model_proxy.enabled())

    def bind(self, token: str) -> None:
        if not token:
            raise ValueError("cannot bind the model proxy without a session token")
        self._env.update({PROXY_KEY: token, LEGACY_PROXY_KEY: ""})
        model_proxy.configure(self._config)

    def unbind(self) -> None:
        self._env.update({PROXY_KEY: "", LEGACY_PROXY_KEY: ""})
        model_proxy.configure(self._config)

"""ProviderSettings — the knobs an image provider needs that are not per call.

agent.toml [plugins.comfy-bridge.providers.<provider>] over the machine config's: Higgsfield's
plan price per credit (`usd_per_credit`), its OAuth `client_id`, a `workspace`. Read per call —
config is per run, and one plugin instance serves every run.
"""

from __future__ import annotations

from agent_runtime.application.run_context import current_plugins

PLUGIN = "comfy-bridge"


class ProviderSettings:
    def __init__(self, config) -> None:
        self._config = config

    def get(self, provider: str) -> dict:
        out: dict = {}
        for src in (getattr(self._config, "plugins", None) or {}, current_plugins()):
            entry = (((src or {}).get(PLUGIN) or {}).get("providers") or {}).get(provider)
            if isinstance(entry, dict):
                out.update(entry)
        return out


__all__ = ["ProviderSettings"]

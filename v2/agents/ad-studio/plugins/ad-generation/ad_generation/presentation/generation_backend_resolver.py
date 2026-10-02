"""Which provider and model a generation tool uses THIS call.

Per-call argument > agent.toml [plugins.ad-generation.tools.<tool>] > global config. Nothing is
defaulted in code: a tool with no provider or model configured says exactly where to set one.
"""

from __future__ import annotations

from agent_runtime.application.run_context import current_plugins
from agent_runtime.application.tool_models import resolve_tool_model, resolve_tool_provider

PLUGIN = "ad-generation"


def provider_settings(config, provider: str) -> dict:
    """agent.toml [plugins.ad-generation.providers.<provider>] over the machine config's — the
    knobs a provider adapter needs that are not per tool (a plan's credit price, an OAuth
    client id, a workspace). Read per call: config is per run."""
    out: dict = {}
    for src in (getattr(config, "plugins", None) or {}, current_plugins()):
        section = ((src or {}).get(PLUGIN) or {}).get("providers") or {}
        entry = section.get(provider)
        if isinstance(entry, dict):
            out.update(entry)
    return out


class GenerationBackendResolver:
    def __init__(self, config) -> None:
        self._config = config

    def resolve(self, tool: str, provider: str, model: str) -> tuple[str, str]:
        chosen_provider = resolve_tool_provider(self._config, PLUGIN, tool, per_call=provider or None)
        chosen_model = resolve_tool_model(self._config, PLUGIN, tool, per_call=model or None)
        if not chosen_provider or not chosen_model:
            raise ValueError(
                f"{tool} has no {'provider' if not chosen_provider else 'model'}: pass one, or set "
                f"[plugins.{PLUGIN}.tools.{tool}] provider/model in agent.toml"
            )
        return chosen_provider, chosen_model

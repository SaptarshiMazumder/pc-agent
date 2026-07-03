"""The models layer — the ONE modular place ANY model is resolved, CONFIG-ONLY (not env).

The BRAIN (reasoning) model comes from `brain_model(config, agent_model)`; every TOOL's model/provider/
knob comes from the plugins map below. Both read only from `config` (+ the per-agent RunContext) —
no environment variable ever feeds a model. If no agentd.config.json was loaded, model resolution
fails LOUD (ConfigMissingError) rather than silently defaulting. Decoupled: this module imports only
run_context; callers ask it for a model instead of reading config fields or env directly.

Tool knobs are keyed **plugins -> tools -> <knob>**. A plugin is just a namespace + a set of tools; all
knobs live on the TOOL. A tool declares its identity (`plugin`, `name`) + built-in defaults in code and
reads its config here, so it can be retuned from `agentd.config.json` or per-agent `agent.toml` WITHOUT
editing Python. Precedence (first hit wins):

    per-call arg  >  agent.toml plugins[P].tools[T].<knob>  >  config plugins[P].tools[T].<knob>  >  built-in default

So agent.toml always wins over global config, and there is NO plugin-level default layer — the plugin is
pure grouping. `model`/`provider` treat an empty value as "unset" (fall through); `tool_config` is
presence-based so a knob explicitly set to false/"" is honored. `description` fields are for humans
reading the config; the resolvers ignore them. Model values are litellm "provider/model" ids (bare id
=> gemini).
"""

from __future__ import annotations

from agentd.application.run_context import current_plugins


class ConfigMissingError(RuntimeError):
    """Raised when a model is requested but no agentd.config.json was loaded. Models come ONLY from
    config (never env), so with no config file there is nothing to resolve — fail loud, don't guess."""


def brain_model(config, agent_model: str | None = None) -> str:
    """THE single entry point for the reasoning (brain) model — config-only, never env.
    per-agent override (agent.toml `model`) -> config.model. Raises ConfigMissingError when no config
    file was loaded (config.config_path is empty) or config has no `model`, so a missing config is
    LOUD instead of a silent default. Decoupled: reads only `config` attributes, nothing else."""
    if agent_model:
        return agent_model
    if not getattr(config, "config_path", ""):
        raise ConfigMissingError(
            "agentd.config.json not found — the brain model comes ONLY from config. Create "
            "v2/agentd.config.json (or set AGENTD_CONFIG) with a top-level \"model\".")
    m = getattr(config, "model", None)
    if not m:
        raise ConfigMissingError("agentd.config.json has no top-level \"model\".")
    return m


def _tool_entry(plugins: dict, plugin: str, tool: str) -> dict | None:
    """The config dict for one tool — plugins[plugin].tools[tool] — or None if absent/malformed."""
    p = (plugins or {}).get(plugin)
    if not isinstance(p, dict):
        return None
    t = (p.get("tools") or {}).get(tool)
    return t if isinstance(t, dict) else None


def _resolve_field(config, plugin: str, tool: str, key: str,
                   per_call: str | None, default: str | None) -> str | None:
    """Precedence for a knob whose EMPTY value means 'unset' (model/provider): per-call ->
    agent.toml tools[tool][key] -> config tools[tool][key] -> default."""
    if per_call:
        return per_call
    plugin = (plugin or "").lower()
    for src in (current_plugins(), getattr(config, "plugins", None) or {}):
        t = _tool_entry(src, plugin, tool)
        if t and t.get(key):
            return t[key]
    return default


def resolve_tool_model(config, plugin: str, tool: str, per_call: str | None = None,
                       default: str | None = None) -> str | None:
    """Resolve the MODEL for `tool` (in `plugin`). See module docstring for precedence."""
    return _resolve_field(config, plugin, tool, "model", per_call, default)


def resolve_tool_provider(config, plugin: str, tool: str, per_call: str | None = None,
                          default: str | None = None) -> str | None:
    """Resolve the backend PROVIDER for `tool` (e.g. imagegen: gemini|fal|replicate; web_search: the
    search chain; browser: playwright|agent_browser). Tool-level, same precedence as the model."""
    return _resolve_field(config, plugin, tool, "provider", per_call, default)


def tool_config(config, plugin: str, tool: str, key: str, default=None):
    """Generic per-tool knob accessor for ANY key (voice, max_steps, headless, ...), PRESENCE-based so
    an explicit false/"" is honored: agent.toml tools[tool][key] -> config tools[tool][key] -> default.
    This is how any tool reads its own config from the one plugins block."""
    plugin = (plugin or "").lower()
    for src in (current_plugins(), getattr(config, "plugins", None) or {}):
        t = _tool_entry(src, plugin, tool)
        if t is not None and key in t:
            return t[key]
    return default


# --- convenience resolvers for model-bearing SUBSYSTEMS -----------------------------------
# The web-search grounder, the verify judge, the egress gate, the resource describers, computer-use,
# and the memory/skills embedders aren't Tool classes but they DO pick a model. They live in the same
# `plugins` map (one place for every model), and each encodes its historical FALLBACK CHAIN in the
# `default` it passes: config plugins.<X>.tools.<Y> wins, else the chain, else the brain. Built-in
# defaults (the last link) live here as constants.
COMPUTER_DEFAULT_MODEL = "gemini-2.5-computer-use-preview-10-2025"
MEMORY_EMBED_DEFAULT_MODEL = "gemini/text-embedding-004"
RESOURCE_VISION_DEFAULT_MODEL = "gemini-2.5-flash"


def search_model(config) -> str | None:
    """Web-search grounding model: plugins.web.tools.web_search.model -> the brain (`model`).
    Kept a FAST model on purpose (a heavy reasoning model blows the web_search timeout)."""
    return resolve_tool_model(config, "web", "web_search", default=getattr(config, "model", None))


def verify_model(config) -> str | None:
    """Judge model for verify_answer / completeness: plugins.verify.tools.verify -> search -> brain."""
    return resolve_tool_model(config, "verify", "verify", default=search_model(config))


def safe_to_send_model(config) -> str | None:
    """Egress-gate judge: plugins.safe_to_send.tools.safe_to_send -> verify -> search -> brain."""
    return resolve_tool_model(config, "safe_to_send", "safe_to_send", default=verify_model(config))


def resource_summary_model(config) -> str | None:
    """Resource text-summary model: plugins.resources.tools.summarize -> verify chain -> brain."""
    return resolve_tool_model(config, "resources", "summarize", default=verify_model(config))

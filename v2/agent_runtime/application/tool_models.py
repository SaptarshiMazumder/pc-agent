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

from agent_runtime.application.run_context import current_plugins

# --- WHOSE config is this? (the per-account seam) --------------------------------------------
# Every function here takes a `config` — and on a hosted daemon that object is the MACHINE's,
# shared by every tenant. The account that owns the current connection may have overridden the
# brain model, a tool's model, or a whole plugin block, and those overrides live in their own
# file (infrastructure/account_config.py). Resolving them is I/O, which this layer may not do,
# so infrastructure INJECTS the resolver at boot and this module simply asks.
#
# Unset (tests, the CLI, any embedder of the package) => identity => the config passed in, which
# is the behaviour every caller had before per-account config existed. The seam is deliberately
# one function and one hook: put the account lookup in each caller instead and the next tool
# added would silently read the wrong tenant's model.
_effective_config = None


def set_effective_config_resolver(fn) -> None:
    """Install the account-aware resolver (``account_config.effective``). Called once, from the
    container. ``None`` restores the identity behaviour."""
    global _effective_config
    _effective_config = fn


def _cfg(config):
    """The config to actually read: the caller's own, per account, or the one handed in."""
    if _effective_config is None or config is None:
        return config
    try:
        return _effective_config(config)
    except Exception:  # noqa: BLE001 — a resolver fault must never break a turn
        return config


class ConfigMissingError(RuntimeError):
    """Raised when a model is requested but no agentd.config.json was loaded. Models come ONLY from
    config (never env), so with no config file there is nothing to resolve — fail loud, don't guess."""


def brain_model(config, agent_model: str | None = None) -> str:
    """THE single entry point for the reasoning (brain) model — config-only, never env.
    per-agent override (agent.toml `model`) -> config.model. Raises ConfigMissingError when no config
    file was loaded (config.config_path is empty) or config has no `model`, so a missing config is
    LOUD instead of a silent default. Decoupled: reads only `config` attributes, nothing else."""
    config = _cfg(config)  # the CALLER's config: their overlay over the machine's
    if agent_model:
        return agent_model
    if not getattr(config, "config_path", ""):
        raise ConfigMissingError(
            "agentd.config.json not found — the brain model comes ONLY from config. Create "
            'v2/agent_runtime.config.json (or set AGENTD_CONFIG) with a top-level "model".'
        )
    m = getattr(config, "model", None)
    if not m:
        raise ConfigMissingError('agentd.config.json has no top-level "model".')
    return m


def _tool_entry(plugins: dict, plugin: str, tool: str) -> dict | None:
    """The config dict for one tool — plugins[plugin].tools[tool] — or None if absent/malformed."""
    p = (plugins or {}).get(plugin)
    if not isinstance(p, dict):
        return None
    t = (p.get("tools") or {}).get(tool)
    return t if isinstance(t, dict) else None


def _resolve_field(
    config, plugin: str, tool: str, key: str, per_call: str | None, default: str | None
) -> str | None:
    """Precedence for a knob whose EMPTY value means 'unset' (model/provider): per-call ->
    agent.toml tools[tool][key] -> config tools[tool][key] -> default."""
    if per_call:
        return per_call
    config = _cfg(config)  # per-account tool models live here too
    plugin = (plugin or "").lower()
    for src in (current_plugins(), getattr(config, "plugins", None) or {}):
        t = _tool_entry(src, plugin, tool)
        if t and t.get(key):
            return t[key]
    return default


# SEED per-KIND default models — a model-bearing tool declares its `model_kind` and gets the house
# model for that kind WITHOUT ever naming one. ``config.model_defaults[<kind>]`` OVERRIDES these per
# key (config-first); the seed is only the fresh-install fallback. "text" is absent ON PURPOSE — text
# tools inherit the brain. Change a kind's default for EVERY tool of that kind in ONE config line.
KIND_DEFAULT_MODELS = {
    "vision": "gemini/gemini-3.1-pro-preview",  # reads/understands images
    "image-gen": "gemini/gemini-3-pro-image",  # GENERATES images (Nano Banana Pro)
    "embedding": "gemini/text-embedding-004",
}


def kind_default_model(config, kind: str | None) -> str | None:
    """The house default model for a model KIND: ``config.model_defaults[kind]`` (override) else the
    KIND_DEFAULT_MODELS seed. None for an empty/unknown kind (e.g. 'text' => inherit the brain)."""
    if not kind:
        return None
    md = getattr(_cfg(config), "model_defaults", None) or {}
    return md.get(kind) or KIND_DEFAULT_MODELS.get(kind)


def resolve_tool_model(
    config,
    plugin: str,
    tool: str,
    per_call: str | None = None,
    default: str | None = None,
    kind: str | None = None,
) -> str | None:
    """Resolve the MODEL for `tool`. Precedence (first hit wins): per-call arg > agent.toml
    plugins[P].tools[T].model > config plugins[P].tools[T].model > the tool's OWN `default_model`
    (a considered per-tool choice, e.g. verify_figure's cheap judge) > the HOUSE DEFAULT for the
    tool's KIND (config.model_defaults). So a tool that names NO model just declares `model_kind`
    and inherits the house default; a tool with a specific need declares `default_model`; and either
    is overridable from config per-tool (plugins.<p>.tools.<t>.model) or per-kind (model_defaults)."""
    hit = _resolve_field(config, plugin, tool, "model", per_call, None)
    if hit:
        return hit
    return default or kind_default_model(config, kind)


def resolve_tool_provider(
    config, plugin: str, tool: str, per_call: str | None = None, default: str | None = None
) -> str | None:
    """Resolve the backend PROVIDER for `tool` (e.g. figure-art: gemini|fal|replicate; web_search: the
    search chain; browser: playwright|agent_browser). Tool-level, same precedence as the model."""
    return _resolve_field(config, plugin, tool, "provider", per_call, default)


def tool_config(config, plugin: str, tool: str, key: str, default=None):
    """Generic per-tool knob accessor for ANY key (voice, max_steps, headless, ...), PRESENCE-based so
    an explicit false/"" is honored: agent.toml tools[tool][key] -> config tools[tool][key] -> default.
    This is how any tool reads its own config from the one plugins block."""
    config = _cfg(config)  # per-account tool models live here (plugins.<p>.tools.<t>.model)
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


# --- convenience resolvers for behavioral (non-model) TOOL knobs ----------------------------
# The `browser` and `computer` tools each carry MANY tuning knobs (headless, channel, max_steps,
# ...). These thin wrappers over tool_config keep the (plugin, tool) pair out of every call site;
# any other tool reads its own knob via tool_config(config, plugin, tool, key, default) directly.
# Presence-based (tool_config), so an explicit false/0/"" set in config is honored, and the
# built-in default is the last link — the tool still works with an empty/absent plugins block.


def browser_knob(config, key: str, default=None):
    """A behavioral knob for the `browser` tool: plugins.browser.tools.browser.<key> (headless,
    persistent, downloads, channel, stealth, cursor_scan, chrome_profile, console_buffer,
    action_timeout_ms, cdp_url, agent_browser_command)."""
    return tool_config(config, "browser", "browser", key, default=default)


def computer_knob(config, key: str, default=None):
    """A behavioral knob for the `computer` tool: plugins.computer.tools.computer.<key> (max_steps,
    send_max, capture, pause, call_timeout_seconds, save_screenshots, corral_to_primary)."""
    return tool_config(config, "computer", "computer", key, default=default)

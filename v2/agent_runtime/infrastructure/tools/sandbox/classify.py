"""The CLASSIFIER — one decision point: which tools are untrusted, and wrap those in a sandbox.

``classify_origin`` reads a tool's provenance tags (set by plugin discovery) to decide its
``PluginOrigin``. The rule is simple and config-overridable (never hardcoded): a tool that rode in
inside an agent's own package — ``agents/<id>/plugins/`` (tagged ``_agent_id``) — is
THIRD_PARTY_BUNDLE (untrusted). Everything else (the shipped standard library, operator-installed
plugins) is FIRST_PARTY. An operator can exempt a specific plugin id via
``config.sandbox_trusted_plugins`` for local development.

``wrap_untrusted`` is the single place the wrapping happens: untrusted tools become ``SandboxedTool``
bound to the given backend + resolver; trusted tools pass through unchanged. Off => identity, so the
seam is dormant until ``config.sandbox_untrusted_plugins`` is enabled.
"""

from __future__ import annotations

import logging

from agent_runtime.application.interfaces.plugin_sandbox import CapabilityResolver, PluginSandbox
from agent_runtime.application.interfaces.tool import Tool
from agent_runtime.domain.sandbox import PluginOrigin

from .sandboxed_tool import SandboxedTool

log = logging.getLogger("agentd")


def _trusted_ids(config) -> set[str]:
    return {str(x).strip() for x in (getattr(config, "sandbox_trusted_plugins", None) or ()) if str(x).strip()}


def classify_origin(tool: Tool, config=None) -> PluginOrigin:
    """Trust provenance for one tool, from the tags plugin discovery stamped on it."""
    plugin_id = getattr(tool, "_plugin_id", "") or ""
    if config is not None and plugin_id and plugin_id in _trusted_ids(config):
        return PluginOrigin.FIRST_PARTY
    # An agent-private plugin (agents/<id>/plugins/) is the untrusted, marketplace-authored tier.
    if getattr(tool, "_agent_id", ""):
        return PluginOrigin.THIRD_PARTY_BUNDLE
    return PluginOrigin.FIRST_PARTY


def wrap_untrusted(
    tools: list[Tool],
    *,
    sandbox: PluginSandbox,
    resolver: CapabilityResolver,
    config=None,
) -> list[Tool]:
    """Wrap every untrusted tool in a SandboxedTool; leave trusted tools as-is.

    Returns the list unchanged when sandboxing is disabled — the caller can always route tools
    through here; the flag decides whether anything is actually wrapped.
    """
    if not getattr(config, "sandbox_untrusted_plugins", False):
        return tools
    out: list[Tool] = []
    for t in tools:
        origin = classify_origin(t, config)
        if origin.is_untrusted:
            plugin_id = getattr(t, "_plugin_id", "") or "unknown"
            out.append(
                SandboxedTool(t, sandbox, resolver, plugin_id=plugin_id, origin=origin)
            )
            log.info(
                "sandbox: wrapping untrusted tool '%s' (plugin '%s') via %s backend",
                getattr(t, "name", "?"),
                plugin_id,
                getattr(sandbox, "name", "?"),
            )
        else:
            out.append(t)
    return out

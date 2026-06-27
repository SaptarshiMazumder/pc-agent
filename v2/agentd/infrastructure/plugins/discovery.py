"""Discover installed plugins and turn them into tools.

Two roots (mirrors OpenClaw's extensions/ + node_modules/@openclaw/*):
  1. a drop-in DIR -- ``<plugins_dir>/<id>/plugin.toml``  (default <V2_ROOT>/plugins,
     override with AGENTD_PLUGINS_DIR), and
  2. pip ENTRY POINTS -- ``group="agentd.plugins"`` (``pip install agentd-plugin-x``).

The per-plugin LOAD GATE (config ``plugins[id]``) is checked against the manifest BEFORE any
code is imported, so a disabled plugin's (heavy) deps never load. ``kind="mcp"`` is recognized
but deferred to phase 3 (it routes through the existing MCP provider)."""

from __future__ import annotations

import logging
from pathlib import Path

from agentd.infrastructure.plugins.loader import load_plugin_entry
from agentd.infrastructure.plugins.manifest import PluginManifest, load_manifest

log = logging.getLogger("agentd")


def discover_plugin_contributions(config) -> tuple[list, list, list]:
    """Everything enabled, installed plugins contribute: ``(tools, prompt_sections, mcp_servers)``.

    - native plugins  -> tools (+ optional prompt sections), via their entry.
    - mcp plugins     -> an McpServerConfig (its tools appear when the gateway connects it),
      PLUS optional prompt sections if the plugin also declares an ``entry`` (e.g. the google
      plugin contributes its ## Google accounts block this way).
    """
    tools: list = []
    sections: list = []
    servers: list = []
    for manifest in _discover_manifests(config):
        if manifest.kind == "mcp":
            servers.append(_mcp_server_config(manifest))
        if manifest.entry:                     # native, or mcp-with-entry (prompt sections etc.)
            t, s = load_plugin_entry(manifest, config)
            tools.extend(t)
            sections.extend(s)
    return tools, sections, servers


def discover_plugin_tools(config) -> list:
    """Back-compat: just the tools (callers that don't need sections/servers)."""
    return discover_plugin_contributions(config)[0]


def _mcp_server_config(manifest: PluginManifest):
    """An mcp plugin's ``[mcp]`` block -> an McpServerConfig the existing provider connects to."""
    from agentd.config import McpServerConfig
    mcp = manifest.mcp or {}
    return McpServerConfig(
        name=manifest.id,
        transport="http" if mcp.get("url") else "stdio",
        command=mcp.get("command"),
        env=mcp.get("env"),
        url=mcp.get("url"),
        headers=mcp.get("headers"),
    )


def _gate(config, plugin_id: str, author_default: bool) -> bool:
    """config.plugins[id] overrides the manifest's own `enabled`; absent => the author default."""
    plugins = getattr(config, "plugins", None) or {}
    return bool(plugins.get(plugin_id, author_default))


def _discover_manifests(config) -> list[PluginManifest]:
    out: list[PluginManifest] = []
    seen: set[str] = set()

    # 1. drop-in directory  (guard the empty string -> Path("") is ".", which we must NOT scan)
    raw_dir = getattr(config, "plugins_dir", "") or ""
    d = Path(raw_dir)
    if raw_dir and d.is_dir():
        for sub in sorted(d.iterdir()):
            mf = sub / "plugin.toml"
            if not (sub.is_dir() and mf.is_file()):
                continue
            m = load_manifest(mf)
            if m is None or m.id in seen:
                continue
            if not _gate(config, m.id, m.enabled):
                log.info("plugins: '%s' disabled by config", m.id)
                continue
            seen.add(m.id)
            out.append(m)

    # 2. pip entry points
    out.extend(_entrypoint_manifests(config, seen))
    return out


def _entrypoint_manifests(config, seen: set[str]) -> list[PluginManifest]:
    out: list[PluginManifest] = []
    try:
        from importlib.metadata import entry_points
        eps = list(entry_points(group="agentd.plugins"))
    except Exception as e:  # noqa: BLE001 — entry-point lookup never breaks discovery
        log.debug("plugins: entry-point discovery skipped: %s", e)
        return out
    for ep in eps:
        if ep.name in seen:
            continue
        if not _gate(config, ep.name, True):
            log.info("plugins: '%s' (entry point) disabled by config", ep.name)
            continue
        entry = ep.value if ":" in ep.value else f"{ep.value}:register"
        seen.add(ep.name)
        out.append(PluginManifest(id=ep.name, name=ep.name, kind="native", entry=entry, root=None))
    return out

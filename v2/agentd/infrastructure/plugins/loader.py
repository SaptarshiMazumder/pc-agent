"""Load a native plugin's tools from its manifest (import the entry, call register()).

Never raises: a plugin that fails to import/register is logged and skipped so others and the
gateway are unaffected (same graceful-degradation pattern as the browser/MCP factories)."""

from __future__ import annotations

import importlib
import logging
import sys

from agentd.application.interfaces.plugins import PluginContext
from agentd.infrastructure.plugins.api import CollectingPluginApi
from agentd.infrastructure.plugins.manifest import PluginManifest

log = logging.getLogger("agentd")


def load_plugin_entry(manifest: PluginManifest, config) -> tuple[list, list]:
    """Import ``manifest.entry`` ("module:func"), call ``func(api, ctx)``, and return the
    plugin's contributions: ``(tools, prompt_sections)``. Used for BOTH native plugins (tools +
    sections) and mcp plugins that ALSO have an entry (sections only — tools come from the
    server). The plugin's own dir is put on ``sys.path`` first so a drop-in folder is importable."""
    module_name, _, func_name = manifest.entry.partition(":")
    func_name = func_name or "register"
    try:
        root = str(manifest.root) if manifest.root is not None else ""
        if root and root not in sys.path:
            sys.path.insert(0, root)          # make a drop-in <plugins_dir>/<id>/ importable
        module = importlib.import_module(module_name)
        register = getattr(module, func_name, None)
        if not callable(register):
            log.warning("plugins: '%s' entry %s has no callable %s", manifest.id,
                        module_name, func_name)
            return [], []
        api = CollectingPluginApi()
        register(api, PluginContext(config=config, plugin_dir=root))
        log.info("plugins: loaded plugin '%s' (%d tool(s), %d prompt section(s))",
                 manifest.id, len(api.tools), len(api.prompt_sections))
        return api.tools, api.prompt_sections
    except Exception as e:  # noqa: BLE001 — a broken plugin must never break the rest
        log.warning("plugins: failed to load plugin '%s': %s", manifest.id, e)
        return [], []

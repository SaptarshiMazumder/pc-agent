"""CollectingPluginApi — the concrete PluginApi handed to a native plugin's register().

It just accumulates the tools the plugin registers, which the loader reads back. This is the
seam where new capability kinds (channels, providers) would be added later (OCP)."""

from __future__ import annotations

import logging

log = logging.getLogger("agentd")


class CollectingPluginApi:
    """Implements the application ``PluginApi`` Protocol by collecting registered tools."""

    def __init__(self):
        self.tools: list = []

    def register_tool(self, tool) -> None:
        # duck-typed: a tool just needs a .name and .execute (the existing Tool contract).
        if tool is None or not getattr(tool, "name", ""):
            log.warning("plugins: register_tool ignored a tool with no name")
            return
        self.tools.append(tool)

"""CollectingPluginApi — the concrete PluginApi handed to a native plugin's register().

It just accumulates the tools the plugin registers, which the loader reads back. This is the
seam where new capability kinds (channels, providers) would be added later (OCP)."""

from __future__ import annotations

import logging

log = logging.getLogger("agentd")


class CollectingPluginApi:
    """Implements the application ``PluginApi`` Protocol by collecting a plugin's contributions
    (tools + prompt sections), which the loader reads back."""

    def __init__(self):
        self.tools: list = []
        self.prompt_sections: list = []   # callables (tools, agent, config) -> str

    def register_tool(self, tool) -> None:
        # duck-typed: a tool just needs a .name and .execute (the existing Tool contract).
        if tool is None or not getattr(tool, "name", ""):
            log.warning("plugins: register_tool ignored a tool with no name")
            return
        self.tools.append(tool)

    def register_prompt_section(self, section) -> None:
        if callable(section):
            self.prompt_sections.append(section)
        else:
            log.warning("plugins: register_prompt_section ignored a non-callable")

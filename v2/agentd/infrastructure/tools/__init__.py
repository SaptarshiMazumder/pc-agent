"""Tools — the FRAMEWORK side of the agent's capabilities.

Every tool *implementation* now lives OUTSIDE the core, in ``plugins/`` — built-in capability
bundles (the standard library: planning, core_fs, shell, web, autonomy, memory, skills, resources,
browser, computer, verify) + any third-party plugins, all discovered at runtime by
``agentd.infrastructure.plugins``. The core contributes NO tool implementations; ``build_tools`` is
gone (the catalog is assembled entirely by plugin discovery — see ``main/container.py``).

This package keeps only the tool FRAMEWORK, re-exported here so ``from agentd.infrastructure.tools
import Tool`` keeps working:

  * ``Tool`` / ``ToolResult`` / ``OnUpdate`` / ``ToolArgError`` / ``validate_args`` — the contract,
    canonically defined in ``agentd.application.interfaces.tool`` (clean-arch home: an interface
    depending only on domain types). This is the surface plugins import.

Sibling framework modules: ``guard`` (the reliability middleware), ``mcp/`` (the MCP client), and
the injected provider subpackages ``browser/`` + ``computer/`` (the shared sessions the container
builds and hands to the browser/computer plugins via DI).
"""

from __future__ import annotations

# The tool contract lives in the application layer now; re-exported for back-compat.
from agentd.application.interfaces.tool import (  # noqa: F401
    OnUpdate,
    Tool,
    ToolArgError,
    ToolResult,
    validate_args,
)

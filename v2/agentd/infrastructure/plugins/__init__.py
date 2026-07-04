"""Plugin system — discover separately-installed tools (drop-in dir + pip entry points)
and contribute them to the catalog as ordinary Tools. See
planning/platform/tools/plugin-catalog-architecture.md."""

from agentd.infrastructure.plugins.discovery import (
    discover_plugin_contributions,
    discover_plugin_tools,
)
from agentd.infrastructure.plugins.entitlement import (
    AllowAllEntitlement,
    LicenseEntitlement,
    build_entitlement,
)
from agentd.infrastructure.plugins.manifest import PluginManifest, load_manifest

__all__ = [
    "discover_plugin_contributions",
    "discover_plugin_tools",
    "AllowAllEntitlement",
    "LicenseEntitlement",
    "build_entitlement",
    "PluginManifest",
    "load_manifest",
]

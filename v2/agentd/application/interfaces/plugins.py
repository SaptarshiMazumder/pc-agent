"""Plugin contracts — what a downloadable tool plugin implements to register itself.

A plugin is a separately-installed unit that contributes tool(s) to the catalog. Once its
tools land in the catalog they are ordinary guarded ``Tool``s, indistinguishable from the
built-in ones (see planning/platform/tools/plugin-catalog-architecture.md).

DECOUPLED (DIP): a native plugin depends ONLY on ``PluginApi`` + the existing ``Tool``
contract — never on the loader, the catalog, or the container. ``register_tool`` is
duck-typed on the tool (``.name`` / ``.execute``) so this interface imports nothing from
infrastructure and the import-linter contract stays intact.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class PluginContext:
    """Read-only handles a plugin's ``register`` may need. Kept minimal on purpose; new
    fields are additive (a plugin only reads what it uses)."""
    config: object                 # the app Config — for the plugin's OWN settings/keys


@runtime_checkable
class PluginApi(Protocol):
    """Handed to a plugin so it can contribute capabilities. Today: tools. Later, additively:
    register_channel / register_provider / register_gateway_method (OCP — tool plugins never
    need to change when new kinds are added)."""
    def register_tool(self, tool) -> None: ...


@runtime_checkable
class Plugin(Protocol):
    """What a NATIVE plugin module exposes (referenced by the manifest's ``entry``)."""
    def register(self, api: PluginApi, ctx: PluginContext) -> None: ...

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
    fields are additive (a plugin only reads what it uses).

    DEPENDENCY INJECTION: beyond config + its own dir, a plugin can receive the SAME runtime
    singletons the built-in tools get — so a plugin tool is a first-class peer that can drive the
    shared browser, persist to the cron ledger, read memory, etc. Every handle is OPTIONAL (None
    when that subsystem is off), so a plugin guards on what it needs and the core stays decoupled
    (this interface still imports nothing concrete — the handles are duck-typed ``object``)."""
    config: object                 # the app Config — for the plugin's OWN settings/keys
    plugin_dir: str = ""           # the plugin's OWN folder — resolve bundled scripts/data here
    browser: object = None         # BrowserManager — the shared browser session
    computer: object = None        # computer-use provider (screen/keyboard/mouse driver)
    task_store: object = None      # durable cron/task ledger
    memory_bank: object = None     # long-term memory store
    resource_manager: object = None  # workspace resource index + CRUD
    credential_store: object = None  # saved-login vault (no plaintext secrets ever reach the model)
    connect_token_store: object = None  # channel connect tokens
    registry: object = None        # AgentRegistry — list/author agents at runtime (create_agent)
    register_plugin_live: object = None  # callable() -> hot-load NEWLY-written plugins into the
    #                                      live catalog without a restart (used by create_tool)

    def resource(self, name: str) -> str:
        """Absolute path to a file bundled in the plugin's folder (its declared scripts/data,
        or anything alongside its code). Cleaner + relocation-proof vs ``Path(__file__).parent``."""
        from pathlib import Path
        return str(Path(self.plugin_dir) / name) if self.plugin_dir else name


@runtime_checkable
class PluginApi(Protocol):
    """Handed to a plugin so it can contribute capabilities. Today: tools + prompt sections.
    Later, additively: register_channel / register_provider / register_gateway_method (OCP —
    existing plugins never change when new kinds are added)."""
    def register_tool(self, tool) -> None: ...

    def register_prompt_section(self, section) -> None:
        """Contribute a system-prompt instruction block. ``section`` is a callable
        ``(tools, agent, config) -> str`` invoked each turn; it returns the block to inject
        (or "" to add nothing — e.g. gate on whether this plugin's tools are present). This is
        how a plugin teaches the model how to use its tools WITHOUT any core prompt code."""
        ...


@runtime_checkable
class Plugin(Protocol):
    """What a NATIVE plugin module exposes (referenced by the manifest's ``entry``)."""
    def register(self, api: PluginApi, ctx: PluginContext) -> None: ...


@runtime_checkable
class EntitlementPolicy(Protocol):
    """One of the FOUR plugin load gates — the DISTRIBUTION seam (architecture, not billing).

    Loading a plugin requires all four to pass: it must be *installed* (a manifest is discovered),
    *enabled* (config / manifest default), *compatible* (its declared os/bins/env are present), and
    *entitled* — this gate. The default policy entitles everything (open-source behavior); a
    commercial deployment injects a policy that consults the tenant's plan/licence to gate optional
    or paid bundles, WITHOUT the core (or any plugin) knowing how that decision is made.

    ``manifest`` is duck-typed (has ``.id`` / ``.name`` / ``.kind``) so this interface imports
    nothing concrete.
    """
    def is_entitled(self, manifest) -> bool: ...

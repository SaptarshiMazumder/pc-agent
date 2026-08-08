"""BundleDefaults — derive a bundle's identity from the agent that is being packed.

Pure: dicts in, a ``BundleManifest`` out. No filesystem, no zip, no registry.

WHY THIS EXISTS AT ALL
``agentd bundle pack`` takes a bundle's identity from an optional ``bundle.toml`` sitting in the
agent dir, and falls back to *the directory name* and *"1.0.0"*. It never reads ``agent.toml``.
So an agent whose ``agent.toml`` says ``version = "2.3.0"`` packs as 1.0.0 — and since installs
supersede BY VERSION, every later release silently fails to replace the first one. Deriving the
manifest from ``agent.toml`` is the fix: the version the author bumped is the version that ships.

PRECEDENCE — most specific wins:
    explicit argument  >  bundle.toml  >  agent.toml  >  fallback

``bundle.toml`` still wins over ``agent.toml`` because it is the publisher-facing file: it is
where a publisher name, an entitlement SKU, or a bundle id that differs from the agent id belong.
It just stops being the only source of the things ``agent.toml`` already states.
"""

from __future__ import annotations

from agent_runtime.domain.bundle import BundleManifest, PluginDep

DEFAULT_VERSION = "1.0.0"


def _first(*values: str) -> str:
    """The first non-empty, stripped value — the precedence chain in one place."""
    for v in values:
        s = str(v or "").strip()
        if s:
            return s
    return ""


class BundleDefaults:
    """Builds the manifest for one agent. Stateless; a class for symmetry with the rule objects."""

    name = "bundle-defaults"

    def manifest(
        self,
        agent_id: str,
        agent_toml: dict,
        bundle_toml: dict,
        private_plugin_ids: tuple[str, ...] = (),
        version: str = "",
    ) -> BundleManifest:
        """``bundle_toml`` is the ``[bundle]`` table (``{}`` when the file is absent).

        ``private_plugin_ids`` are the agent's OWN plugins (``agents/<id>/plugins/``). They are
        NOT declared as dependencies: they live inside the agent directory, so they are already
        inside the zip's ``agent/`` tree. Declaring them would make the installer look for
        vendored copies at ``plugins/<pid>/`` that were never written. They are accepted here
        only so the caller can report what is riding along.
        """
        bundle_id = _first(bundle_toml.get("id"), agent_id)
        return BundleManifest(
            id=bundle_id,
            name=_first(bundle_toml.get("name"), agent_toml.get("name"), bundle_id),
            # The whole point: the author's own version, not a directory-name-shaped guess.
            version=_first(
                version, bundle_toml.get("version"), agent_toml.get("version"), DEFAULT_VERSION
            ),
            description=_first(bundle_toml.get("description"), agent_toml.get("description")),
            agentd_compat=_first(bundle_toml.get("agentd_compat")),
            entitlement=_first(bundle_toml.get("entitlement")),
            publisher=_first(bundle_toml.get("publisher")),
            # `icon` in [app] is an installer artifact (an .ico path); the bundle's icon is a
            # store-card GLYPH NAME. Different things — only take an explicitly declared one.
            icon=_first(bundle_toml.get("icon")),
            plugins=self._deps(bundle_toml),
        )

    @staticmethod
    def _deps(bundle_toml: dict) -> tuple[PluginDep, ...]:
        """SHARED plugin dependencies, declared in bundle.toml's [[bundle.plugins]]. Rows that
        name no id are dropped rather than packed as a dependency on nothing."""
        out: list[PluginDep] = []
        for raw in bundle_toml.get("plugins") or []:
            if not isinstance(raw, dict):
                continue
            dep_id = str(raw.get("id") or "").strip()
            if not dep_id:
                continue
            out.append(
                PluginDep(
                    id=dep_id,
                    source=str(raw.get("source") or "vendored").strip(),
                    package=str(raw.get("package") or "").strip(),
                    version=str(raw.get("version") or "").strip(),
                )
            )
        return tuple(out)

"""pack_agent_dir — agent directory -> .agentpkg. The one definition of what packing means.

MOVED HERE FROM THE CLI. This was `bundle.py::_pack_agent_dir`, a private function in a command
module, which made it unreachable for anything that was not a command: the product builder needs
it, and so does the publish service. Reaching into a CLI module from an application service would
have inverted the layering, and copying it would have created a second answer to "what is in a
bundle?" — the artifact `pack` produces and the artifact `publish` uploads have to be the same
bytes, or an author tests one thing and ships another.

`agentd bundle pack` and `agentd bundle publish` both still call exactly this.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from agent_runtime.domain.bundle import BundleManifest, PluginDep, parse_bundle_manifest


def load_bundle_toml(agent_dir: Path) -> dict:
    """The PUBLISHER-facing file's [bundle] table. ``{}`` when there is none."""
    path = agent_dir / "bundle.toml"
    if not path.is_file():
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8")).get("bundle") or {}


def load_agent_toml(agent_dir: Path) -> dict:
    """The agent's own declaration. ``{}`` when absent or unparseable — packing a directory
    without a readable agent.toml must still work, it just has fewer defaults to draw on."""
    path = agent_dir / "agent.toml"
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def first(*values) -> str:
    """The first non-empty, stripped value — the precedence chain in one place."""
    for v in values:
        s = str(v or "").strip()
        if s:
            return s
    return ""


def pack_agent_dir(
    agent_dir: Path,
    out_dir: Path,
    version: str = "",
    vendor_plugins: str = "",
    builtin_plugins: str = "",
) -> Path:
    """agent directory -> .agentpkg in out_dir. Raises ValueError with a user-facing message."""
    from agent_runtime.config import load_config
    from agent_runtime.infrastructure.marketplace import bundle_io

    if not agent_dir.is_dir():
        raise ValueError(f"not a directory: {agent_dir}")
    declared = load_bundle_toml(agent_dir)
    # agent.toml is the SECOND tier of every identity field. Without it, an agent whose
    # agent.toml says version = "2.3.0" packed as 1.0.0 — and since installs supersede BY
    # VERSION, every later release silently failed to replace the first one on a buyer's
    # machine. That bites hardest through the per-agent installer path, which calls this to
    # build the payload: the author bumps a version, ships, and nobody ever receives it.
    #
    # PRECEDENCE (matches agent-authoring's BundleDefaults, which fixed the same bug on the
    # chat path only):  explicit argument > bundle.toml > agent.toml > fallback
    # bundle.toml stays on top because it is the PUBLISHER-facing file — publisher name,
    # entitlement SKU, a bundle id that differs from the agent id. It just stops being the
    # only source of what agent.toml already states.
    agent_toml = load_agent_toml(agent_dir)
    bundle_id = first(declared.get("id"), agent_dir.name)
    version = first(version, declared.get("version"), agent_toml.get("version"), "1.0.0")

    deps: list[PluginDep] = []
    if declared:  # bundle.toml is the source of truth for declared deps
        deps = list(
            parse_bundle_manifest(
                {"bundle": {**declared, "id": bundle_id, "version": version}}
            ).plugins
        )
    vendor_ids = [p for p in (vendor_plugins or "").split(",") if p.strip()]
    builtin_ids = [p for p in (builtin_plugins or "").split(",") if p.strip()]
    have = {d.id for d in deps}
    deps += [
        PluginDep(id=p.strip(), source="vendored") for p in vendor_ids if p.strip() not in have
    ]
    deps += [
        PluginDep(id=p.strip(), source="builtin") for p in builtin_ids if p.strip() not in have
    ]

    # Only VENDORED deps need this install's plugin roots, so only they need the config. Loading it
    # unconditionally made packing depend on the ambient environment for no reason — a bundle with
    # no vendored plugins is a pure function of its directory.
    vendored_dirs: dict[str, Path] = {}
    vendored = [d for d in deps if d.source == "vendored"]
    if vendored:
        config = load_config()
        plugin_roots = [Path(config.plugins_dir), Path(config.builtin_plugins_dir)]
        for dep in vendored:
            source_dir = next(
                (r / dep.id for r in plugin_roots if (r / dep.id / "plugin.toml").is_file()), None
            )
            if source_dir is None:
                raise ValueError(
                    f"vendored plugin '{dep.id}' not found in {', '.join(map(str, plugin_roots))}"
                )
            vendored_dirs[dep.id] = source_dir

    manifest = BundleManifest(
        id=bundle_id,
        name=first(declared.get("name"), agent_toml.get("name"), bundle_id),
        version=version,
        description=first(declared.get("description"), agent_toml.get("description")),
        agentd_compat=str(declared.get("agentd_compat") or ""),
        entitlement=str(declared.get("entitlement") or ""),
        publisher=str(declared.get("publisher") or ""),
        icon=str(declared.get("icon") or ""),
        plugins=tuple(deps),
    )
    return bundle_io.pack_bundle(agent_dir, out_dir, manifest, vendored_dirs)

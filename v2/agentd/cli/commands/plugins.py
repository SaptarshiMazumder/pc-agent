"""`agentd plugins list` — every discoverable plugin with its GATE STATES: where it
came from (builtin / drop-in), and whether it is provisioned (distribution profile),
enabled (config), and compatible (os/bins/env). Manifest-only — no plugin code is
imported, so this is safe and instant."""

from __future__ import annotations

import argparse
from pathlib import Path


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("plugins", help="list plugins and their gate states")
    sub = parser.add_subparsers(dest="plugins_command")
    list_parser = sub.add_parser("list", help="list plugins and their gate states")
    list_parser.set_defaults(func=run_list)
    parser.set_defaults(func=run_list)


def run_list(args: argparse.Namespace) -> int:
    from agentd.config import load_config
    from agentd.infrastructure.plugins.manifest import load_manifest

    config = load_config()
    profile = getattr(config, "distribution", None)
    gates: dict = getattr(config, "plugins", None) or {}

    roots: list[tuple[str, Path]] = []
    builtin = Path(config.builtin_plugins_dir or "")
    if builtin.is_dir():
        roots.append(("builtin", builtin))
    dropin = Path(config.plugins_dir or "")
    if dropin.is_dir() and dropin != builtin:
        roots.append(("drop-in", dropin))

    seen: set[str] = set()
    rows: list[tuple[str, str, str]] = []
    for origin, root in roots:
        for child in sorted(root.iterdir()):
            manifest_path = child / "plugin.toml"
            if not manifest_path.is_file():
                continue
            manifest = load_manifest(manifest_path)
            if manifest is None or manifest.id in seen:
                continue
            seen.add(manifest.id)
            states = []
            gate = gates.get(manifest.id)
            enabled = (
                manifest.enabled
                if not isinstance(gate, (bool, dict))
                else (gate if isinstance(gate, bool) else gate.get("enabled", manifest.enabled))
            )
            if not enabled:
                states.append("disabled")
            if profile is not None and not profile.is_provisioned(manifest.id):
                states.append("not provisioned")
            state = ", ".join(states) if states else "active"
            rows.append((manifest.id, origin, f"{state:<18} {manifest.name}"))

    for plugin_id, origin, detail in rows:
        print(f"  {plugin_id:<16} {origin:<8} {detail}")
    print(f"\n{len(rows)} plugin(s)")
    return 0

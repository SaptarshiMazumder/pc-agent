"""PluginManifest — the parsed ``plugin.toml`` (pure value object + a tolerant loader).

The manifest declares WHAT a plugin contributes WITHOUT importing the plugin's code, so a
disabled plugin's heavy deps never load (the gate is checked against the manifest, before any
import). One format wraps either a native tool (``entry``) or an MCP server (``[mcp]``)."""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("agentd")

VALID_KINDS = ("native", "mcp")


@dataclass(frozen=True)
class PluginManifest:
    id: str
    name: str
    kind: str                       # "native" | "mcp"
    entry: str = ""                 # native: "module:func" (a register(api, ctx) callable)
    mcp: dict = field(default_factory=dict)   # mcp: {command|url|env|headers}
    enabled: bool = True            # author default; the config plugin-gate overrides this
    scripts: tuple = ()             # declared helper scripts bundled in the plugin folder
    data: tuple = ()                # declared data files bundled in the plugin folder
    root: Path | None = None        # the plugin's directory (added to sys.path for native)
    # COMPATIBILITY gate (one of the 4 load gates): a plugin is skipped unless its platform /
    # binaries / env are present. Keys: "os" (platform allowlist, e.g. ["windows","linux"]),
    # "bins" (all on PATH), "env" (all set). Empty => always compatible. From [requires] in the toml.
    requires: dict = field(default_factory=dict)
    # DISTRIBUTION metadata (tiers doc §4) — pure description, no loader behavior:
    # tier: "core" | "bundled" | "addon" ("" = unspecified); entitlement: the license SKU
    # that unlocks this plugin ("" = free — every entitlement policy allows it). From
    # [distribution] in the toml.
    tier: str = ""
    entitlement: str = ""


def load_manifest(path: Path) -> PluginManifest | None:
    """Parse one ``plugin.toml`` -> PluginManifest, or None (logged) on any problem.
    Validates the bare minimum: a valid id, a known kind, and the field that kind needs."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 — a bad manifest never breaks discovery
        log.warning("plugins: bad manifest %s: %s", path, e)
        return None
    pid = str(data.get("id") or "").strip()
    kind = str(data.get("kind") or "").strip().lower()
    if not pid:
        log.warning("plugins: manifest %s has no id", path)
        return None
    if kind not in VALID_KINDS:
        log.warning("plugins: manifest %s has invalid kind %r", path, kind)
        return None
    if kind == "native" and not str(data.get("entry") or "").strip():
        log.warning("plugins: native plugin '%s' needs an `entry`", pid)
        return None
    root = path.parent
    scripts = tuple(str(s).strip() for s in (data.get("scripts") or []) if str(s).strip())
    files = tuple(str(s).strip() for s in (data.get("data") or []) if str(s).strip())
    for rel in (*scripts, *files):                   # declared-but-missing => warn (never fatal)
        if not (root / rel).exists():
            log.warning("plugins: '%s' declares missing asset %r", pid, rel)
    raw_req = dict(data.get("requires") or {})
    requires = {k: [str(x).strip().lower() if k == "os" else str(x).strip()
                    for x in (raw_req.get(k) or []) if str(x).strip()]
                for k in ("os", "bins", "env")}
    requires = {k: v for k, v in requires.items() if v}     # keep only declared keys
    distribution = dict(data.get("distribution") or {})
    return PluginManifest(
        id=pid,
        name=str(data.get("name") or pid),
        kind=kind,
        entry=str(data.get("entry") or "").strip(),
        mcp=dict(data.get("mcp") or {}),
        enabled=bool(data.get("enabled", True)),
        scripts=scripts,
        data=files,
        root=root,
        requires=requires,
        tier=str(distribution.get("tier") or "").strip().lower(),
        entitlement=str(distribution.get("entitlement") or "").strip(),
    )

"""PluginManifest — the parsed ``plugin.toml`` (pure value object + a tolerant loader).

The manifest declares WHAT a plugin contributes WITHOUT importing the plugin's code, so a
disabled plugin's heavy deps never load (the gate is checked against the manifest, before any
import). One format wraps either a native tool (``entry``) or an MCP server (``[mcp]``)."""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from agent_runtime.domain.sandbox_net import PLACEHOLDER as _PLACEHOLDER

log = logging.getLogger("agentd")

VALID_KINDS = ("native", "mcp")


@dataclass(frozen=True)
class PluginManifest:
    id: str
    name: str
    kind: str  # "native" | "mcp"
    description: str = ""  # one-line "what this plugin is" (from plugin.toml; else the
    # module docstring, resolved at load). "" => client default.
    entry: str = ""  # native: "module:func" (a register(api, ctx) callable)
    mcp: dict = field(default_factory=dict)  # mcp: {command|url|env|headers}
    enabled: bool = True  # author default; the config plugin-gate overrides this
    scripts: tuple = ()  # declared helper scripts bundled in the plugin folder
    data: tuple = ()  # declared data files bundled in the plugin folder
    root: Path | None = None  # the plugin's directory (added to sys.path for native)
    # COMPATIBILITY gate (one of the 4 load gates): a plugin is skipped unless its platform /
    # binaries / env are present. Keys: "os" (platform allowlist, e.g. ["windows","linux"]),
    # "bins" (all on PATH), "env" (all set). Empty => always compatible. From [requires] in the toml.
    requires: dict = field(default_factory=dict)
    # SANDBOX GRANTS — what this plugin needs when it runs UNTRUSTED, from [sandbox] in the toml.
    # Deliberately NOT folded into `requires`: that is a GATE ("skip me unless this is present"),
    # this is a REQUEST ("when you box me in, leave these open"). One table answering both
    # questions would make "declared but missing" mean two incompatible things.
    #   net     -> hosts the DAEMON will call on the plugin's behalf (it never gets a socket)
    #   secrets -> credential NAMES it may reference as ${NAME}; it never receives the values
    # Empty/absent => no network and no credentials, which is the default and stays the default.
    sandbox: dict = field(default_factory=dict)
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
    for rel in (*scripts, *files):  # declared-but-missing => warn (never fatal)
        if not (root / rel).exists():
            log.warning("plugins: '%s' declares missing asset %r", pid, rel)
    raw_req = dict(data.get("requires") or {})
    requires = {
        k: [
            str(x).strip().lower() if k == "os" else str(x).strip()
            for x in (raw_req.get(k) or [])
            if str(x).strip()
        ]
        for k in ("os", "bins", "env")
    }
    requires = {k: v for k, v in requires.items() if v}  # keep only declared keys
    raw_sbx = dict(data.get("sandbox") or {})
    sandbox = {
        # A real HOST is lowercased so every later comparison is against one canonical form
        # (DNS is case-insensitive). A `${SETTING}` placeholder is NOT: it names a setting key,
        # which is case-sensitive — the plugin writes `${COMFYUI_URL}` in its code and the
        # author declared `COMFYUI_URL`, so lowercasing it here to `${comfyui_url}` makes the
        # broker's resolve miss and every request fail `scheme '(none)'`. This is the same
        # rule `resolve_allowlist` applies; it just has to hold here too, since this runs first.
        # secret NAMES are already case-preserved, and for the same reason.
        "net": [
            s if _PLACEHOLDER.fullmatch(s) else s.lower()
            for x in (raw_sbx.get("net") or [])
            if (s := str(x).strip())
        ],
        "secrets": [str(x).strip() for x in (raw_sbx.get("secrets") or []) if str(x).strip()],
    }
    sandbox = {k: v for k, v in sandbox.items() if v}
    distribution = dict(data.get("distribution") or {})
    return PluginManifest(
        id=pid,
        name=str(data.get("name") or pid),
        kind=kind,
        description=str(data.get("description") or "").strip(),
        entry=str(data.get("entry") or "").strip(),
        mcp=dict(data.get("mcp") or {}),
        enabled=bool(data.get("enabled", True)),
        scripts=scripts,
        data=files,
        root=root,
        requires=requires,
        sandbox=sandbox,
        tier=str(distribution.get("tier") or "").strip().lower(),
        entitlement=str(distribution.get("entitlement") or "").strip(),
    )

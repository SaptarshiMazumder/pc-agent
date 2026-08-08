"""Discover installed plugins and turn them into tools.

Two roots (mirrors OpenClaw's extensions/ + node_modules/@openclaw/*):
  1. a drop-in DIR -- ``<plugins_dir>/<id>/plugin.toml``  (default <V2_ROOT>/plugins,
     override with AGENTD_PLUGINS_DIR), and
  2. pip ENTRY POINTS -- ``group="agentd.plugins"`` (``pip install agentd-plugin-x``).

The per-plugin LOAD GATE (config ``plugins[id]``) is checked against the manifest BEFORE any
code is imported, so a disabled plugin's (heavy) deps never load. ``kind="mcp"`` is recognized
but deferred to phase 3 (it routes through the existing MCP provider)."""

from __future__ import annotations

import logging
from pathlib import Path

from agent_runtime.infrastructure.plugins.loader import load_plugin_entry
from agent_runtime.infrastructure.plugins.manifest import PluginManifest, load_manifest

log = logging.getLogger("agentd")


def discover_plugin_contributions(
    config, deps: dict | None = None, entitlement=None, skip_ids: set | None = None
) -> tuple[list, list, list, list]:
    """Everything enabled, installed plugins contribute:
    ``(tools, prompt_sections, mcp_servers, skill_dirs)``.

    ``skip_ids`` (a MUTABLE set) enables incremental hot-reload: manifests whose id is already
    in it are skipped, and every newly-loaded id is ADDED to it. Pass the same set on each call
    and only plugins added since the last call are loaded (so tools/sections aren't duplicated).
    ``None`` => load everything, track nothing (the one-shot startup behaviour).

    - native plugins  -> tools (+ optional prompt sections), via their entry. A tool self-describes
      via its ``description``; any strong/shared guidance is a PROMPT SECTION the plugin registers
      (``register_prompt_section`` — e.g. the planning/verify/google bundles), gated by the plugin.
    - mcp plugins     -> an McpServerConfig (its tools appear when the gateway connects it),
      PLUS optional prompt sections if the plugin also declares an ``entry``.
    - any plugin      -> its ``skills/`` dir is advertised like any skill folder.

    ``deps`` are injected runtime handles (browser, task_store, …) forwarded to each native
    plugin's PluginContext, so a plugin tool can use the same singletons the built-ins do.
    """
    tools: list = []
    sections: list = []
    servers: list = []
    skill_dirs: list = []
    for manifest in _discover_manifests(config, entitlement):
        if skip_ids is not None and manifest.id in skip_ids:
            continue  # already loaded in a prior (hot-reload) pass
        if skip_ids is not None:
            skip_ids.add(manifest.id)  # mark loaded so the next reload skips it
        if manifest.kind == "mcp":
            servers.append(_mcp_server_config(manifest))
        native_tools, native_sections = ([], [])
        if manifest.entry:  # native, or mcp-with-entry (prompt sections etc.)
            native_tools, native_sections = load_plugin_entry(manifest, config, deps)
        # tag provenance + the plugin's OWN description (plugin.toml, else its module docstring)
        # onto each tool, so the catalog/descriptors self-source it instead of a central config note
        plugin_desc = manifest.description or _module_doc(manifest.entry)
        for t in native_tools:
            for attr, val in (
                ("_plugin_id", manifest.id),
                ("_plugin_name", manifest.name),
                ("_plugin_desc", plugin_desc),
            ):
                try:
                    setattr(t, attr, val)
                except (AttributeError, TypeError):
                    pass
        tools.extend(native_tools)
        sections.extend(native_sections)
        # the plugin's bundled skills (plugins/<id>/skills/) -> advertised like top-level skills.
        if manifest.root is not None:
            sk = manifest.root / "skills"
            if sk.is_dir():
                skill_dirs.append(sk)
    return tools, sections, servers, skill_dirs


def discover_plugin_tools(config) -> list:
    """Back-compat: just the tools (callers that don't need sections/servers/skills)."""
    return discover_plugin_contributions(config)[0]


def discover_agent_plugins(agents_dir, config, deps: dict | None = None, entitlement=None) -> dict:
    """The AGENT-PRIVATE plugin tier: ``agents/<id>/plugins/<pid>/plugin.toml`` ->
    ``{agent_id: [tools]}``.

    A marketplace agent's highly-specialized tools ship INSIDE its own folder, so definition and
    capability travel — and uninstall — together (the folder rides in the .agentpkg exactly like
    ui/ does). The shared ``plugins/`` tier stays for capabilities every agent inherits. Same
    manifest format, same entry loading, same config/compat/entitlement gates as the shared tier;
    the differences are the ROOT (each agent's dir) and that every tool is tagged ``_agent_id``,
    so it is offered ONLY to its owning agent — never to other agents or the global catalog.
    A private plugin contributes TOOLS only (v1): prompt sections / mcp servers / skills belong
    to the shared tier (an agent's own skills already live in ``agents/<id>/skills/``)."""
    out: dict = {}
    root = Path(agents_dir or "")
    if not root.is_dir():
        return out
    for agent_dir in sorted(root.iterdir()):
        pdir = agent_dir / "plugins"
        if not (agent_dir.is_dir() and pdir.is_dir()):
            continue
        agent_id = agent_dir.name.lower()
        tools: list = []
        seen: set[str] = set()
        for sub in sorted(pdir.iterdir()):
            mf = sub / "plugin.toml"
            if not (sub.is_dir() and mf.is_file()):
                continue
            m = load_manifest(mf)
            if m is None or m.id in seen or not m.entry:
                continue
            if not _passes_gates(config, m, entitlement):
                continue
            seen.add(m.id)
            native_tools, _sections = load_plugin_entry(m, config, deps)
            plugin_desc = m.description or _module_doc(m.entry)
            for t in native_tools:
                for attr, val in (
                    ("_plugin_id", m.id),
                    ("_plugin_name", m.name),
                    ("_plugin_desc", plugin_desc),
                    ("_agent_id", agent_id),
                    # WHERE the code came from, carried on the tool itself. This tier is the
                    # untrusted one, and a sandbox backend that runs the tool in another process
                    # cannot pass the live object across — it has to load the plugin again on the
                    # far side, which takes an entry and a directory. Without these two tags the
                    # subprocess backend has no recipe and (correctly) refuses to run the tool.
                    ("_plugin_entry", m.entry),
                    ("_plugin_root", str(m.root) if m.root is not None else ""),
                    # What the plugin DECLARED it needs while sandboxed ([sandbox] in its toml).
                    # Carried on the tool because the capability resolver is handed a tool, not a
                    # manifest — the same reason _plugin_entry rides here. A declaration is a
                    # CEILING: the resolver narrows it by operator config and never widens it.
                    ("_sandbox_net", tuple(m.sandbox.get("net") or ())),
                    ("_sandbox_secrets", tuple(m.sandbox.get("secrets") or ())),
                ):
                    try:
                        setattr(t, attr, val)
                    except (AttributeError, TypeError):
                        pass
            tools.extend(native_tools)
        if tools:
            out[agent_id] = tools
            log.info(
                "plugins: agent '%s' ships %d private tool(s): %s",
                agent_id,
                len(tools),
                ", ".join(getattr(t, "name", "?") for t in tools),
            )
    return out


def _module_doc(entry: str) -> str:
    """First meaningful line of a native plugin's module docstring — the fallback plugin
    description when its `plugin.toml` declares none. The module is already imported by
    `load_plugin_entry`, so this is a cheap `sys.modules` lookup; undocumented/missing => ''."""
    import sys

    from agent_runtime.application.descriptions import first_meaningful_line

    module_name = (entry or "").partition(":")[0]
    mod = sys.modules.get(module_name)
    return first_meaningful_line(getattr(mod, "__doc__", "") or "") if mod else ""


def _mcp_server_config(manifest: PluginManifest):
    """An mcp plugin's ``[mcp]`` block -> an McpServerConfig the existing provider connects to."""
    from agent_runtime.config import McpServerConfig

    mcp = manifest.mcp or {}
    return McpServerConfig(
        name=manifest.id,
        transport="http" if mcp.get("url") else "stdio",
        command=mcp.get("command"),
        env=mcp.get("env"),
        url=mcp.get("url"),
        headers=mcp.get("headers"),
    )


def _gate(config, plugin_id: str, author_default: bool) -> bool:
    """config.plugins[id] overrides the manifest's own `enabled`; absent => the author default.

    `config.plugins` is the ONE per-plugin config block (see config.py): an entry is normally the
    plugin's config DICT (its `model`/`tools`/… knobs), whose OPTIONAL `enabled` key decides — and
    when that key is omitted we fall back to the author default, so configuring a plugin's models
    NEVER silently flips its enablement. A bare bool entry (legacy/shorthand) still enables/disables
    directly. Anything else => the author default."""
    plugins = getattr(config, "plugins", None) or {}
    if plugin_id not in plugins:
        return author_default
    entry = plugins[plugin_id]
    if isinstance(entry, bool):
        return entry
    if isinstance(entry, dict):
        return bool(entry.get("enabled", author_default))
    return author_default


def _compatible(manifest) -> bool:
    """COMPATIBILITY gate: the plugin's declared platform/binaries/env must be present. Mirrors the
    skill `requires` gate. Empty requirements => always compatible. IO (platform/PATH/env)."""
    req = getattr(manifest, "requires", None) or {}
    if not req:
        return True
    import os
    import shutil
    import sys

    oses = req.get("os") or []
    if oses and sys.platform not in oses and _os_family(sys.platform) not in oses:
        return False
    # Resolve declared binaries against the SAME PATH the plugin's subprocess will get —
    # which includes this interpreter's script dir, where a pip-installed launcher lands.
    # Checking the ambient PATH instead would judge a correctly-installed `uvx` missing and
    # skip the plugin, while the spawner would have found it perfectly well.
    from agent_runtime.runtime_paths import launcher_path

    child_path = launcher_path()
    if any(shutil.which(b, path=child_path) is None for b in req.get("bins", [])):
        return False
    if any(not os.environ.get(e) for e in req.get("env", [])):
        return False
    return True


def _os_family(platform: str) -> str:
    """Map sys.platform to a friendly family name a manifest is likely to use."""
    if platform.startswith("win"):
        return "windows"
    if platform == "darwin":
        return "macos"
    if platform.startswith("linux"):
        return "linux"
    return platform


def _entitled(entitlement, manifest) -> bool:
    """ENTITLEMENT gate (the distribution seam): an injected policy decides if this plugin is
    allowed. None => entitle everything (open-source default). Never raises — a broken policy
    fails OPEN (the bundle is treated as entitled) so a policy bug can't silently disable tools."""
    if entitlement is None:
        return True
    try:
        return bool(entitlement.is_entitled(manifest))
    except Exception as e:  # noqa: BLE001
        log.warning(
            "plugins: entitlement check errored for '%s' (allowing): %s",
            getattr(manifest, "id", "?"),
            e,
        )
        return True


def _provisioned(config, manifest) -> bool:
    """PROVISIONED gate (M6, planning/…/plugin-distribution-architecture.md §2): is this
    plugin part of THIS INSTALL's tier? Decided by the distribution profile
    (distribution.toml -> config.distribution); no profile / no plugin list => everything
    is provisioned (the open default — a checkout or plain pip install never loses tools)."""
    profile = getattr(config, "distribution", None)
    if profile is None:
        return True
    try:
        return bool(profile.is_provisioned(manifest.id))
    except Exception:  # noqa: BLE001 — a broken profile fails OPEN, like entitlement
        return True


def _passes_gates(config, manifest, entitlement) -> bool:
    """The plugin LOAD decision — the gate model (installed is implicit: we have a manifest):
    PROVISIONED (distribution profile) + ENABLED (config/manifest) + COMPATIBLE (os/bins/env)
    + ENTITLED (injected policy). Logs WHY a plugin is skipped so a missing tool is debuggable."""
    if not _provisioned(config, manifest):
        log.info("plugins: '%s' skipped — not provisioned in this distribution", manifest.id)
        return False
    if not _gate(config, manifest.id, manifest.enabled):
        log.info("plugins: '%s' disabled by config", manifest.id)
        return False
    if not _compatible(manifest):
        log.info(
            "plugins: '%s' skipped — incompatible (requires=%s)", manifest.id, manifest.requires
        )
        return False
    if not _entitled(entitlement, manifest):
        log.info("plugins: '%s' skipped — not entitled", manifest.id)
        return False
    return True


def _discover_manifests(config, entitlement=None) -> list[PluginManifest]:
    out: list[PluginManifest] = []
    seen: set[str] = set()

    # 1. drop-in DIRECTORIES: the shipped built-in bundles (always) + the user drop-in dir. The
    # built-in root is scanned FIRST and independently of plugins_dir, so overriding plugins_dir
    # never drops the standard library. Same dir listed twice => deduped by id (harmless).
    # (Guard the empty string -> Path("") is ".", which we must NOT scan.)
    for raw_dir in (
        getattr(config, "builtin_plugins_dir", "") or "",
        getattr(config, "plugins_dir", "") or "",
    ):
        d = Path(raw_dir)
        if not (raw_dir and d.is_dir()):
            continue
        for sub in sorted(d.iterdir()):
            mf = sub / "plugin.toml"
            if not (sub.is_dir() and mf.is_file()):
                continue
            m = load_manifest(mf)
            if m is None or m.id in seen:
                continue
            if not _passes_gates(config, m, entitlement):
                continue
            seen.add(m.id)
            out.append(m)

    # 2. pip entry points
    out.extend(_entrypoint_manifests(config, seen, entitlement))
    return out


def _entrypoint_manifests(config, seen: set[str], entitlement=None) -> list[PluginManifest]:
    out: list[PluginManifest] = []
    try:
        from importlib.metadata import entry_points

        eps = list(entry_points(group="agentd.plugins"))
    except Exception as e:  # noqa: BLE001 — entry-point lookup never breaks discovery
        log.debug("plugins: entry-point discovery skipped: %s", e)
        return out
    for ep in eps:
        if ep.name in seen:
            continue
        entry = ep.value if ":" in ep.value else f"{ep.value}:register"
        m = PluginManifest(id=ep.name, name=ep.name, kind="native", entry=entry, root=None)
        if not _passes_gates(config, m, entitlement):
            continue
        seen.add(ep.name)
        out.append(m)
    return out

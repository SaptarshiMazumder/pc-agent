"""Load a native plugin's tools from its manifest (import the entry, call register()).

Never raises: a plugin that fails to import/register is logged and skipped so others and the
gateway are unaffected (same graceful-degradation pattern as the browser/MCP factories).

A PLUGIN MODULE'S IDENTITY IS WHERE IT CAME FROM, not its bare name — see ``_load_entry_module``.
Bare ``import_module`` keyed every plugin by module name in ONE global namespace, which is two
bugs at once on a multi-agent deployment: two agents shipping ``marketing_image.py`` get whichever
loaded first (silently, forever), and re-installing an agent at a new version keeps serving the
OLD code because ``sys.modules`` already holds that name. The second one is not theoretical: an
agent upgraded on a running daemon kept its previous plugin behaviour with the fixed file sitting
on disk, which reads from the outside as "the runtime can't see files that are right there".
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import logging
import sys
from pathlib import Path

from agent_runtime.application.interfaces.plugins import PluginContext
from agent_runtime.infrastructure.plugins.api import CollectingPluginApi
from agent_runtime.infrastructure.plugins.manifest import PluginManifest

log = logging.getLogger("agentd")


def _entry_file(root: str, module_name: str) -> Path | None:
    """The FILE backing ``module_name`` inside this plugin's own root, or None.

    None means the entry is not a file we ship (a pip-installed plugin resolved from
    site-packages) — that keeps its ordinary import, because its identity really is its
    installed name."""
    if not root or not module_name or "." in module_name:
        return None
    base = Path(root)
    for candidate in (base / f"{module_name}.py", base / module_name / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _source_stamp(file: Path) -> str | None:
    """What the plugin's code IS, hashed — the freshness signal, or None if unreadable.

    CONTENT, not mtime: an install that rewrites a plugin within one clock tick leaves the
    timestamp and the size identical, and a stamp that misses that change is the very staleness
    this exists to prevent. Plugin sources are small and this runs at discovery (boot and
    hot-reload), never per tool call. A package hashes every module under it, so changing a
    submodule counts as changing the plugin."""
    try:
        if file.name == "__init__.py":
            parts = sorted(file.parent.rglob("*.py"))
            h = hashlib.sha1()
            for part in parts:
                h.update(str(part.relative_to(file.parent)).encode("utf-8", "replace"))
                h.update(part.read_bytes())
            return h.hexdigest()
        return hashlib.sha1(file.read_bytes()).hexdigest()
    except OSError:
        return None


def _load_entry_module(module_name: str, root: str):
    """Import the entry module under a name derived from its LOCATION, re-executing it when the
    code on disk has changed.

    The namespace is the plugin's root path, so two agents may ship the same module name without
    colliding, and the content stamp is what makes an install of a new version take effect on a
    running daemon instead of being masked by the cached module."""
    file = _entry_file(root, module_name)
    if file is None:
        return importlib.import_module(module_name)  # installed distribution: unchanged
    digest = hashlib.sha1(str(Path(root).resolve()).encode("utf-8", "replace")).hexdigest()[:12]
    unique = f"agentd_plugin_{digest}_{module_name}"
    stamp = _source_stamp(file)
    cached = sys.modules.get(unique)
    if cached is not None and stamp is not None and getattr(cached, "__agentd_stamp__", None) == stamp:
        return cached
    spec = importlib.util.spec_from_file_location(
        unique,
        file,
        submodule_search_locations=[str(file.parent)] if file.name == "__init__.py" else None,
    )
    if spec is None or spec.loader is None:
        return importlib.import_module(module_name)
    module = importlib.util.module_from_spec(spec)
    module.__agentd_stamp__ = stamp
    sys.modules[unique] = module  # before exec: a module that imports itself must find it
    try:
        # COMPILE THE SOURCE OURSELVES rather than letting the loader do it: CPython's bytecode
        # cache validates a .pyc against the source's mtime AND SIZE, so a rewrite that lands in
        # the same clock tick at the same length re-runs the OLD bytecode — the identical
        # staleness one layer down. Executing the bytes we just hashed makes the stamp and the
        # running code the same read, by construction.
        exec(compile(file.read_bytes(), str(file), "exec"), module.__dict__)
    except BaseException:
        sys.modules.pop(unique, None)  # a half-executed module must never be served
        raise
    return module


def load_plugin_entry(
    manifest: PluginManifest, config, deps: dict | None = None
) -> tuple[list, list]:
    """Import ``manifest.entry`` ("module:func"), call ``func(api, ctx)``, and return the
    plugin's contributions: ``(tools, prompt_sections)``. Used for BOTH native plugins (tools +
    sections) and mcp plugins that ALSO have an entry (sections only — tools come from the
    server). The plugin's own dir is put on ``sys.path`` first so a drop-in folder is importable.

    ``deps`` are the injected runtime handles (browser, task_store, …) forwarded onto the
    PluginContext; unknown keys are ignored so adding a handle never breaks an older plugin."""
    from dataclasses import fields

    module_name, _, func_name = manifest.entry.partition(":")
    func_name = func_name or "register"
    try:
        root = str(manifest.root) if manifest.root is not None else ""
        if root and root not in sys.path:
            sys.path.insert(0, root)  # make a drop-in <plugins_dir>/<id>/ importable
        module = _load_entry_module(module_name, root)
        register = getattr(module, func_name, None)
        if not callable(register):
            log.warning(
                "plugins: '%s' entry %s has no callable %s", manifest.id, module_name, func_name
            )
            return [], []
        api = CollectingPluginApi()
        valid = {f.name for f in fields(PluginContext)} - {"config", "plugin_dir"}
        ctx_deps = {k: v for k, v in (deps or {}).items() if k in valid}
        register(api, PluginContext(config=config, plugin_dir=root, **ctx_deps))
        log.info(
            "plugins: loaded plugin '%s' (%d tool(s), %d prompt section(s))",
            manifest.id,
            len(api.tools),
            len(api.prompt_sections),
        )
        return api.tools, api.prompt_sections
    except Exception as e:  # noqa: BLE001 — a broken plugin must never break the rest
        log.warning("plugins: failed to load plugin '%s': %s", manifest.id, e)
        return [], []

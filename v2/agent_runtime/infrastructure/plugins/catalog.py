"""catalog — build the PLUGIN -> TOOL catalog from what tools declare in code.

Shared by two callers that live on DIFFERENT layers, which is why it sits here in
`infrastructure` (both may import DOWN into it): the offline CLI (`agent_runtime.main.list_plugins`)
passes freshly COLD-discovered tools via `build_catalog`; the running gateway
(`agent_runtime.presentation.gateway`) passes the LIVE registered tools. Keeping it below both is what
lets the gateway reuse it WITHOUT the presentation layer importing up into main (the
import-linter clean-layers contract).
"""

from __future__ import annotations

from agent_runtime.application.tool_models import resolve_tool_model
from agent_runtime.infrastructure.plugins.discovery import discover_plugin_tools


def _first_line(text: str, width: int = 100) -> str:
    line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    return line if len(line) <= width else line[: width - 3] + "..."


def _unwrap_tool(tool):
    """Peel reliability wrappers (e.g. GuardedTool, which holds the real tool in ``_inner``) to
    reach the object carrying the discovery metadata — plugin id, needs_model, default_model, the
    full description. A raw/undecorated tool is returned unchanged. Bounded so a cyclic wrapper
    can never spin."""
    seen = 0
    while getattr(tool, "_inner", None) is not None and seen < 8:
        tool = tool._inner
        seen += 1
    return tool


def catalog_from_tools(config, tools) -> dict:
    """{plugin_id: {"description": str, "tools": [{name, needs_model, model, description}]}} for an
    EXPLICIT set of tool objects, with models resolved and config descriptions folded in. Two callers
    share it: the offline CLI passes freshly COLD-discovered tools (``build_catalog``); the running
    gateway passes the LIVE registered tools, so DI-gated tools (browser/computer, the autonomy
    ledger) appear exactly when they're actually loaded — the cold pass can't see them (it injects no
    runtime handles). Tools with no plugin identity (MCP `server__tool`) are skipped: not plugin
    tools, so not part of this plugin-keyed view (and absent from the cold pass too)."""
    cfg_plugins = getattr(config, "plugins", None) or {}
    catalog: dict = {}
    for raw in tools:
        tool = _unwrap_tool(raw)
        pid = getattr(tool, "plugin", "") or getattr(tool, "_plugin_id", "")
        if not pid:
            continue  # not a plugin tool (e.g. an MCP tool)
        entry = catalog.setdefault(pid, {"name": "", "description": "", "tools": []})
        pconf = cfg_plugins.get(pid) if isinstance(cfg_plugins.get(pid), dict) else {}
        entry["name"] = getattr(tool, "_plugin_name", "") or pid
        # description home = the plugin ITSELF (plugin.toml / module docstring, tagged in discovery);
        # the config.json `plugins.<id>.description` note is an optional OVERRIDE when set.
        config_note = pconf.get("description", "") if pconf else ""
        entry["description"] = config_note or getattr(tool, "_plugin_desc", "") or ""
        needs_model = bool(getattr(tool, "needs_model", False))
        model = None
        if needs_model:
            model = resolve_tool_model(
                config,
                pid,
                getattr(tool, "name", ""),
                default=getattr(tool, "default_model", "") or None,
                kind=getattr(tool, "model_kind", "text"),
            )
        tconf = (pconf.get("tools") or {}).get(getattr(tool, "name", "")) if pconf else None
        entry["tools"].append(
            {
                "name": getattr(tool, "name", "?"),
                "needs_model": needs_model,
                "model_kind": getattr(tool, "model_kind", "text") or "text",
                "model": model,
                # self-described PROVIDER options so a client offers a picker, not a text box (empty => free text)
                "provider_options": list(getattr(tool, "provider_options", None) or []),
                "provider_chain": bool(getattr(tool, "provider_chain", False)),
                # self-described canvas ARTIFACT ACTION (e.g. a "Convert to Vector" button on PNGs);
                # {} => not a UI action. Lets a client render the button generically, gated on mime.
                "artifact_action": dict(getattr(tool, "artifact_action", None) or {}),
                "description": (tconf or {}).get("description")
                or _first_line(getattr(tool, "description", "")),
                # the FULL canonical description from the tool's code (the config `description` is a
                # truncated human note); UIs show this, CLIs keep using the short `description`.
                "full_description": (getattr(tool, "description", "") or "").strip(),
            }
        )
    return catalog


def build_catalog(config) -> dict:
    """The catalog from a COLD re-discovery of installed plugins (NO runtime handles injected), so
    DI-gated tools (browser/computer, the autonomy ledger) are absent — fine for the offline CLI,
    which has no running service. The gateway unions this with the LIVE toolset (``catalog_from_tools``
    + ``merge_catalogs``), so those tools show up exactly when actually loaded."""
    return catalog_from_tools(config, discover_plugin_tools(config))


def merge_catalogs(base: dict, extra: dict) -> dict:
    """Union two catalogs (the ``catalog_from_tools`` shape) by plugin id, then by tool name. ``base``
    wins on metadata collisions; plugins/tools present only in ``extra`` are added. The gateway unions
    the COLD-discovered catalog (every installed plugin — INCLUDING tools switched off via
    ``tools_disabled``, which the settings page must still show to re-enable) with the LIVE catalog
    (the DI-gated tools cold discovery can't see), so every configurable tool appears exactly once."""
    out = {
        pid: {
            "name": p.get("name", ""),
            "description": p.get("description", ""),
            "tools": list(p.get("tools") or []),
        }
        for pid, p in base.items()
    }
    for pid, p in extra.items():
        cur = out.get(pid)
        if cur is None:
            out[pid] = {
                "name": p.get("name", ""),
                "description": p.get("description", ""),
                "tools": list(p.get("tools") or []),
            }
            continue
        have = {t.get("name") for t in cur["tools"]}
        cur["tools"].extend(t for t in (p.get("tools") or []) if t.get("name") not in have)
        cur["description"] = cur["description"] or p.get(
            "description", ""
        )  # fill only if base lacked
        cur["name"] = cur["name"] or p.get("name", "")
    return out

"""list_plugins — print the live PLUGIN -> TOOL catalog, generated from what tools declare in code.

  python -m agent_runtime.main.list_plugins            # human tree: every plugin, its tools, resolved models
  python -m agent_runtime.main.list_plugins --json     # machine-readable JSON (for docs / tooling)
  python -m agent_runtime.main.list_plugins --scaffold # ADD every plugin+tool to agentd.config.json's
                                                # "plugins" block (additive; existing knobs preserved)

Nothing here is hand-maintained: it discovers the actual registered tools, reads each tool's own
`plugin` / `needs_model` / `default_model` / `description`, resolves the model a model-bearing tool
would really use via the SAME resolver the runtime uses (config plugins -> tools -> model), and folds
in the human `description` fields from agent_runtime.config.json's `plugins` block. So it can never drift
from the code the way a duplicated list in config would. Provider-gated tools (browser/computer) only
appear when their subsystem is enabled — noted in the output.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent_runtime.config import load_config
from agent_runtime.infrastructure.plugins.catalog import _first_line, build_catalog
from agent_runtime.infrastructure.plugins.discovery import discover_plugin_tools


def build_scaffold(config) -> dict:
    """Merge the LIVE catalog into the existing `plugins` config: every discovered plugin + tool gets an
    entry (its code `description` seeded if none set), while EXISTING knobs (enabled/provider/model/any
    custom key) and ordering are preserved verbatim. Additive + idempotent — re-run any time to pick up
    newly-added tools. (DI-gated tools like browser/computer only load when their subsystem is on, so
    their existing config entries are kept as-is even when not discovered in this run.)"""
    existing = getattr(config, "plugins", None) or {}
    by_plugin: dict = {}
    for tool in discover_plugin_tools(config):
        pid = getattr(tool, "plugin", "") or getattr(tool, "_plugin_id", "") or "(unclassified)"
        by_plugin.setdefault(pid, []).append(tool)

    def _merge_tool(name, discovered, ex_tool):
        ex_tool = ex_tool if isinstance(ex_tool, dict) else {}
        entry = {}
        if "enabled" in ex_tool:
            entry["enabled"] = ex_tool["enabled"]
        entry["description"] = ex_tool.get("description") or (
            _first_line(getattr(discovered, "description", "")) if discovered is not None else ""
        )
        for k, v in ex_tool.items():  # preserve provider/model/any custom knob
            if k not in ("enabled", "description"):
                entry[k] = v
        return entry

    merged: dict = {}
    pids = list(existing) + [
        p for p in sorted(by_plugin) if p not in existing
    ]  # existing order first
    for pid in pids:
        ex = existing.get(pid) if isinstance(existing.get(pid), dict) else {}
        ex_tools = ex.get("tools") if isinstance(ex.get("tools"), dict) else {}
        disc = {getattr(t, "name", ""): t for t in by_plugin.get(pid, [])}
        out: dict = {}
        if "enabled" in ex:
            out["enabled"] = ex["enabled"]
        out["description"] = ex.get("description") or ""
        tool_names = list(ex_tools) + [
            n for n in sorted(disc) if n not in ex_tools
        ]  # existing order first
        out["tools"] = {n: _merge_tool(n, disc.get(n), ex_tools.get(n)) for n in tool_names}
        for k, v in ex.items():  # preserve any other plugin-level key
            if k not in ("enabled", "description", "tools"):
                out[k] = v
        merged[pid] = out
    return merged


def _config_path() -> Path:
    return Path(
        os.environ.get("AGENTD_CONFIG")
        or (Path(__file__).resolve().parents[2] / "agentd.config.json")
    )


def _write_scaffold(config) -> None:
    path = _config_path()
    data = json.loads(path.read_text(encoding="utf-8"))
    data["plugins"] = build_scaffold(config)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    n_p = len(data["plugins"])
    n_t = sum(len(p.get("tools", {})) for p in data["plugins"].values())
    print(f"scaffolded {n_p} plugins / {n_t} tools into {path} (existing knobs + order preserved)")


def _print_tree(catalog: dict) -> None:
    total_tools = sum(len(p["tools"]) for p in catalog.values())
    model_tools = sum(1 for p in catalog.values() for t in p["tools"] if t["needs_model"])
    print(
        f"agentd tool catalog -- {len(catalog)} plugins, {total_tools} tools "
        f"({model_tools} model-bearing)\n"
    )
    for pid in sorted(catalog):
        p = catalog[pid]
        desc = f"  -- {p['description']}" if p["description"] else ""
        print(f"[{pid}]{desc}")
        for t in sorted(p["tools"], key=lambda x: x["name"]):
            tag = f"(model: {t['model']})" if t["needs_model"] else "(no model)"
            print(f"    - {t['name']:<22} {tag}")
            if t["description"]:
                print(f"        {t['description']}")
        print()
    print(
        "Note: models shown are what each tool resolves TODAY from config.plugins (+ built-in\n"
        "defaults). Provider-gated tools (browser/computer) appear only when enabled."
    )


def main() -> None:
    try:  # tool descriptions may hold non-ASCII; force UTF-8 out
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    config = load_config()
    if "--scaffold" in sys.argv[1:]:
        _write_scaffold(config)
        return
    catalog = build_catalog(config)
    if "--json" in sys.argv[1:]:
        print(json.dumps(catalog, indent=2))
    else:
        _print_tree(catalog)


if __name__ == "__main__":
    main()

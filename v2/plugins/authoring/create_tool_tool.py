"""create_tool — author a NEW native tool by chatting, then hot-load it (no restart).

Writes a drop-in plugin: ``<plugins_dir>/<id>/plugin.toml`` + a Python module that defines a
``Tool`` subclass whose ``execute`` runs the agent-supplied ``code``. Then calls the injected
``register_plugin_live`` (the B1 reload seam) so the tool joins the LIVE catalog and is callable
on the next turn.

DANGER — this writes and runs NEW Python in-process (RCE by design). It is gated behind
``AGENTD_TOOL_WORKSHOP`` (off by default); the operator opts in by env, exactly like the other
authoring creators. Prefer create_skill (no code) or add_mcp (a server) when they fit; reach for
create_tool only when a genuinely new in-process capability is needed."""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")


def _ident(name: str) -> str:
    """A safe python identifier for the tool name / module (snake_case)."""
    s = re.sub(r"[^a-z0-9_]+", "_", (name or "").strip().lower()).strip("_")
    return s or "tool"


_MODULE_TEMPLATE = '''\
"""Agent-authored tool (created at runtime by create_tool). Edit with care."""

from agent_runtime.application.interfaces.tool import Tool, ToolResult


class GeneratedTool(Tool):
    name = {name!r}
    label = {label!r}
    default_retryable = False
    description = {description!r}
    parameters = {parameters!r}

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
{body}
        except Exception as e:  # noqa: BLE001 — never let an authored tool crash the loop
            return ToolResult.text(f"{name} failed: {{type(e).__name__}}: {{e}}", is_error=True)


def register(api, ctx):
    api.register_tool(GeneratedTool())
'''


class CreateToolTool(Tool):
    name = "create_tool"
    label = "Create Tool"
    default_retryable = False
    description = (
        "Author a NEW native tool by chatting and load it live (no restart). Give an `id` "
        "(kebab-case), the tool `name` the model will call, a `description` (when/how to use "
        "it), a `parameters` JSON-schema object, and `code` — the Python body of its execute "
        "step: `params` holds the call args, and you MUST end by returning "
        '`ToolResult.text("...")` (or with is_error=True). Use stdlib / already-installed '
        "packages. Pass `agent` to give the tool to ONE agent (it lands in that agent's own "
        "folder, is offered only to it, and needs no [tools] allow entry); omit `agent` for a "
        "SHARED tool every agent inherits. Prefer create_skill (no code) or add_mcp (a server) "
        "when they fit — this runs NEW code in-process, so use it only for a genuinely new "
        "capability."
    )
    parameters = {
        "type": "object",
        "required": ["id", "name", "code"],
        "properties": {
            "id": {"type": "string", "description": "plugin id, kebab-case (e.g. word-count)"},
            "agent": {
                "type": "string",
                "description": "agent id that OWNS this tool -> agents/<id>/plugins/<pid>/ "
                "(private to that agent). Omit for a shared tool in the global catalog.",
            },
            "name": {
                "type": "string",
                "description": "tool name the model calls (e.g. word_count)",
            },
            "description": {"type": "string", "description": "what the tool does + when to use it"},
            "parameters": {
                "type": "object",
                "description": "JSON-schema for the tool's params (default: no params)",
            },
            "code": {
                "type": "string",
                "description": "Python execute body; `params` = args; must return ToolResult.text(...)",
            },
        },
    }

    def __init__(self, config, register_plugin_live, registry=None):
        self._config = config
        self._reload = register_plugin_live
        self._registry = registry  # needed to scope a tool to ONE agent (agents_dir + id check)

    def _resolve_root(self, agent_id: str) -> tuple:
        """Where the new plugin dir goes: the AGENT-PRIVATE tier when an agent is named,
        else the shared catalog (unchanged default). Returns ``(root, error)``.

        The agents dir comes from the REGISTRY, not config, so the write path is the same
        one ``discover_agent_plugins`` scans on reload (container._agent_private_tools)."""
        if not agent_id:
            root = (
                getattr(self._config, "plugins_dir", "")
                or getattr(self._config, "builtin_plugins_dir", "")
                or ""
            )
            if not root:
                return None, "no plugins dir configured (set AGENTD_PLUGINS_DIR)"
            return Path(root), None
        if self._registry is None:
            return None, "agent-scoped tools need the agent registry — omit `agent` for a shared tool"
        try:
            known = self._registry.list_ids()
        except Exception as e:  # noqa: BLE001 — a registry failure must not crash the turn
            return None, f"could not read the agent roster ({type(e).__name__}: {e})"
        if agent_id not in known:
            return None, f"unknown agent '{agent_id}' (known: {', '.join(sorted(known)) or 'none'})"
        agents_dir = getattr(self._registry, "agents_dir", None)
        if not agents_dir:
            return None, "the registry exposes no agents_dir — cannot place an agent-private tool"
        return Path(agents_dir) / agent_id / "plugins", None

    async def execute(self, tool_call_id, params, abort, on_update=None):
        pid = _slug(params.get("id", ""))
        if not pid:
            return ToolResult.text("create_tool needs an 'id' (kebab-case)", is_error=True)
        tool_name = _ident(params.get("name", "") or pid)
        code = (params.get("code") or "").strip("\n")
        if not code:
            return ToolResult.text("create_tool needs 'code' (the execute body)", is_error=True)
        description = (params.get("description") or f"The {tool_name} tool.").strip()
        schema = params.get("parameters")
        if not isinstance(schema, dict):
            schema = {"type": "object", "properties": {}}

        agent_id = _slug(params.get("agent", ""))
        root, err = self._resolve_root(agent_id)
        if err:
            return ToolResult.text(f"cannot write the tool: {err}", is_error=True)
        d = root / pid
        if d.exists():
            return ToolResult.text(
                f"a plugin '{pid}' already exists at {d} — pick a different id", is_error=True
            )

        module = _ident(pid)
        body = textwrap.indent(textwrap.dedent(code), " " * 12)
        source = _MODULE_TEMPLATE.format(
            name=tool_name,
            label=tool_name.replace("_", " ").title(),
            description=description,
            parameters=schema,
            body=body,
        )
        # Compile-check the generated module BEFORE writing it, so a syntax error is reported
        # to the model instead of leaving a broken plugin on disk.
        try:
            compile(source, f"<create_tool:{pid}>", "exec")
        except SyntaxError as e:
            return ToolResult.text(
                f"generated tool has a syntax error (line {e.lineno}): {e.msg} — fix the `code`",
                is_error=True,
            )

        d.mkdir(parents=True, exist_ok=True)
        (d / f"{module}.py").write_text(source, encoding="utf-8")
        (d / "plugin.toml").write_text(
            f'id = "{pid}"\nname = "{tool_name}"\nkind = "native"\nentry = "{module}:register"\n',
            encoding="utf-8",
        )

        if not callable(self._reload):
            return ToolResult.text(
                f"wrote tool '{tool_name}' at {d}, but live reload is unavailable — restart to load it",
                is_error=True,
            )
        result = self._reload()
        if not result.get("ok"):
            return ToolResult.text(
                f"wrote tool '{tool_name}' at {d}, but reload failed: {result.get('error')}",
                is_error=True,
            )
        # Which side of the reload report to check: a SHARED tool shows up in `tools`, an
        # agent-private one in `agentTools` ({agentId: count}) — the two tiers are reported
        # separately, so checking `tools` for a scoped tool would always falsely warn.
        if agent_id:
            loaded = bool((result.get("agentTools") or {}).get(agent_id))
            where = f"private to agent '{agent_id}'"
        else:
            loaded = tool_name in (result.get("tools") or [])
            where = "shared (every agent inherits it)"
        return ToolResult.text(
            f"Created tool '{tool_name}' (plugin '{pid}') at {d} — {where} — and loaded it live"
            + ("" if loaded else " (note: not visible in the catalog — check enablement)")
            + " — callable next turn.",
            details={"id": pid, "tool": tool_name, "agent": agent_id or ""},
        )

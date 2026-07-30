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
        "packages. Prefer create_skill (no code) or add_mcp (a server) when they fit — this "
        "runs NEW code in-process, so use it only for a genuinely new capability."
    )
    parameters = {
        "type": "object",
        "required": ["id", "name", "code"],
        "properties": {
            "id": {"type": "string", "description": "plugin id, kebab-case (e.g. word-count)"},
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

    def __init__(self, config, register_plugin_live):
        self._config = config
        self._reload = register_plugin_live

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

        root = (
            getattr(self._config, "plugins_dir", "")
            or getattr(self._config, "builtin_plugins_dir", "")
            or ""
        )
        if not root:
            return ToolResult.text(
                "no plugins dir configured (set AGENTD_PLUGINS_DIR) — cannot write the tool",
                is_error=True,
            )
        d = Path(root) / pid
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
        loaded = tool_name in (result.get("tools") or [])
        return ToolResult.text(
            f"Created tool '{tool_name}' (plugin '{pid}') at {d} and loaded it live"
            + ("" if loaded else " (note: not visible in the catalog — check enablement)")
            + " — callable next turn.",
            details={"id": pid, "tool": tool_name},
        )

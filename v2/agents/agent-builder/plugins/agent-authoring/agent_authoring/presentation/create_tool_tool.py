"""create_tool — author a NEW native tool by chatting, then hot-load it (no restart).

Writes a drop-in plugin: ``<plugins_dir>/<id>/plugin.toml`` + a Python module that defines a
``Tool`` subclass whose ``execute`` runs the agent-supplied ``code``. Then calls the injected
``register_plugin_live`` (the B1 reload seam) so the tool joins the LIVE catalog and is callable
on the next turn.

DANGER — this writes and runs NEW Python in-process (RCE by design). It used to be gated behind
``AGENTD_TOOL_WORKSHOP``; the gate is now the AGENT BOUNDARY. This bundle is private to
agents/agent-builder/, so only that agent can call it, and an install without that agent has no
route to it at all. Prefer a skill (no code) or add_mcp (a server) when they fit; reach for
create_tool only when a genuinely new in-process capability is needed.

SANDBOX-CORRECTNESS IS DECIDED HERE, NOT BY THE CALLER. An agent-scoped tool is trusted on this
machine and UNTRUSTED on every machine that installs the agent, so the shape it must have to work
after shipping is knowable and finite — and leaving it to the model to remember produced exactly
the failure you would expect. Two mechanisms, both mechanical (``domain/sandbox_contract.py``):

  * ``needs_model`` is DERIVED from the code. A body that calls ``text_complete`` /
    ``vision_complete`` gets the attribute stamped on the class, because that flag is the only
    thing that puts models on the sandbox grant — without it the broker refuses every call with
    "not granted", for every buyer, silently for the author.
  * The two shapes that CANNOT work once installed — reading ``os.environ`` and importing a
    network client — are REFUSED before anything is written, with the fix in the message. Same
    handling as the existing compile-check: a defect the caller can act on beats a file on disk
    that only breaks somewhere else.

``agent`` IS REQUIRED, and that is what makes the two mechanisms above mean anything. Omitting it
used to write a SHARED tool into the operator's machine-wide catalog — a tier that is never
classified untrusted, so it skipped both refusals AND the filesystem fence that stops this agent
writing outside the agent it was asked to build. One argument was the difference between "checked"
and "unchecked", the refusal message above named it as the way out, and AGENTS.md meanwhile told
the model that create_tool refused everything outside the target agent. Building one agent never
needs a machine-wide tool; wiring an EXISTING shared tool into a built agent is a ``[tools] allow``
entry in its agent.toml and does not come through here. Authoring a new shared tool is a change to
the whole machine and belongs to the operator, by hand."""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

from agent_authoring.domain.sandbox_contract import blocking_defects, derive_model_need
from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.write_scope import WriteRefused, check_write


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
    # DERIVED from the code below by create_tool, not chosen by hand. `plugin` is the config
    # grouping this tool is addressable under ([plugins.<plugin>.tools.<name>] in agent.toml or
    # agentd.config.json); `needs_model` is what puts models on the sandbox grant at all.
    plugin = {plugin!r}
    needs_model = {needs_model!r}
    model_kind = {model_kind!r}

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
        "packages. `agent` is REQUIRED and names the agent that owns the tool: it lands in that "
        "agent's own folder, is offered only to it, and needs no [tools] allow entry. There is no "
        "machine-wide option — to give an agent a capability the shared catalog ALREADY has, name "
        "that existing tool in its [tools] allow rather than writing a new one. "
        "Prefer create_skill (no code) or add_mcp (a server) "
        "when they fit — this runs NEW code in-process, so use it only for a genuinely new "
        "capability.\n"
        "TO CALL A MODEL from the tool, use `from agent_runtime.infrastructure.llm.oneshot "
        "import text_complete` (or vision_complete) inside the code — that is the ONE route "
        "that also works once the agent is installed, because the host makes the call on the "
        "tool's behalf. This tool then sets needs_model itself; you do not declare it. Never "
        "call a provider's HTTP API directly and never read a key from the environment: an "
        "agent-scoped tool that does either is REFUSED here, because it would work for you and "
        "silently do nothing for everyone who downloads the agent."
    )
    parameters = {
        "type": "object",
        "required": ["id", "name", "code", "agent"],
        "properties": {
            "id": {"type": "string", "description": "plugin id, kebab-case (e.g. word-count)"},
            "agent": {
                "type": "string",
                "description": "REQUIRED — agent id that OWNS this tool -> agents/<id>/plugins/"
                "<pid>/, private to that agent. There is no machine-wide option: to give an "
                "agent a tool that already exists in the shared catalog, name it in that "
                "agent's [tools] allow instead of writing a new one.",
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

    def __init__(self, register_plugin_live, registry=None):
        self._reload = register_plugin_live
        # REQUIRED in practice: every tool is scoped to one agent, and the agents_dir + id check
        # comes from here. It stays optional only so a minimal test can construct the tool and
        # get the refusal rather than a TypeError. The daemon's config used to be injected too,
        # for the shared plugins dir — nothing resolves there any more.
        self._registry = registry

    def _resolve_root(self, agent_id: str) -> tuple:
        """Where the new plugin dir goes: always the AGENT-PRIVATE tier. Returns ``(root, error)``.

        The agents dir comes from the REGISTRY, not config, so the write path is the same
        one ``discover_agent_plugins`` scans on reload (container._agent_private_tools)."""
        if not agent_id:
            return None, (
                "create_tool needs 'agent' — the id of the agent that will own this tool. "
                "Every tool written here is private to one agent. If the capability already "
                "exists in the shared catalog, do not write a new tool: name the existing one "
                "in that agent's [tools] allow. Authoring a machine-wide shared tool is the "
                "operator's decision and is not done through this tool."
            )
        if self._registry is None:
            return None, "agent-scoped tools need the agent registry, which is not available here"
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

        # A tool written here becomes untrusted the moment someone installs the agent, so the
        # shapes that cannot survive that are refused now, before the file exists. There is no
        # longer a tier this check can be routed around — see the module docstring.
        defects = blocking_defects(code)
        if defects:
            lines = [
                f"REFUSING to write '{tool_name}' — this code cannot work once someone "
                f"installs agent '{agent_id}'. A private tool is UNTRUSTED on their machine:",
                "",
            ]
            for _code, what, fix in defects:
                lines += [f"  • it {what}", f"    -> {fix}", ""]
            lines.append(
                "Rewrite the code so it works under the sandbox and call create_tool again. "
                "The fix above is the supported route: a tool that needs a model asks through "
                "oneshot, and a tool that needs a key takes it from [[settings]]. If neither "
                "shape can express what this tool does, say so to the user and stop — do not "
                "look for a tier where the check does not apply."
            )
            return ToolResult.text(
                "\n".join(lines),
                is_error=True,
                details={"refused": [c for c, _w, _f in defects], "agent": agent_id},
            )

        # needs_model is read OFF THE CODE. It is the whole authorisation for a sandboxed model
        # call, and asking the caller to remember an attribute it has no reason to know about is
        # how tools shipped that were refused by the broker on every buyer's machine.
        needs_model, model_kind = derive_model_need(code)

        module = _ident(pid)
        body = textwrap.indent(textwrap.dedent(code), " " * 12)
        source = _MODULE_TEMPLATE.format(
            name=tool_name,
            label=tool_name.replace("_", " ").title(),
            description=description,
            parameters=schema,
            plugin=pid,
            needs_model=needs_model,
            model_kind=model_kind,
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

        # THE SAME SCOPE `write` ENFORCES. This tool opens files itself, so without this it is a
        # way straight past the block. The shared-plugins escape it used to guard is gone —
        # `agent` is required — but the check stays: it is the fence, and a fence that only holds
        # while one caller happens to behave is not one.
        try:
            check_write(d)
        except WriteRefused as e:
            return ToolResult.text(
                f"{e}\n\nA tool is written inside the agent that owns it. Check that `agent` "
                f"names the agent you are building.",
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
        # Agent-private tools are reported in `agentTools` ({agentId: count}), separately from the
        # shared `tools` list — checking the wrong side would always falsely warn.
        loaded = bool((result.get("agentTools") or {}).get(agent_id))
        where = f"private to agent '{agent_id}'"
        # Say that the model wiring was decided FOR the caller, and how to change it. A tool that
        # silently acquires an attribute is one nobody knows to configure later.
        model_note = (
            f"\nIt calls a model, so needs_model=True and model_kind='{model_kind}' were set for "
            f"you — that is what lets the host serve its model calls when the agent is installed "
            f"and sandboxed. Pin which model with "
            f"[plugins.{pid}.tools.{tool_name}] model = \"...\" in the agent's agent.toml."
            if needs_model
            else ""
        )
        return ToolResult.text(
            f"Created tool '{tool_name}' (plugin '{pid}') at {d} — {where} — and loaded it live"
            + ("" if loaded else " (note: not visible in the catalog — check enablement)")
            + " — callable next turn."
            + model_note,
            details={
                "id": pid,
                "tool": tool_name,
                "agent": agent_id or "",
                "needsModel": needs_model,
                "modelKind": model_kind,
            },
        )

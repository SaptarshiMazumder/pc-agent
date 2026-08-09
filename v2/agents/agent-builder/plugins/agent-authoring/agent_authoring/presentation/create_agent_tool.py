"""create_agent — SCAFFOLD a new persistent AGENT, then get out of the way.

"An agent is a directory": this writes ``agents/<id>/`` (``agent.toml`` + ``IDENTITY.md``, plus
``AGENTS.md`` when operating rules are given), then registers it in the LIVE registry via
``registry.add(id)`` — the inverse of the registry's ``remove()`` — so the new agent is
resolvable on the next message WITHOUT a restart. The files it writes are exactly what
``FileAgentRegistry`` / ``load_bootstrap`` read, so a by-chat agent is identical to a
hand-authored one.

DELIBERATELY FROZEN AT THE SKELETON — do not add parameters for the rest of agent.toml.

This tool owns the half of an agent that genuinely needs code:
  * correct TOML KEY ORDER — every top-level key must precede the first ``[table]``, or TOML
    scopes it INTO that table and it is silently ignored (the exact bug that made an authored
    ``color``/``tagline`` under ``[app]`` vanish in favour of a generated sidecar value),
  * refusing to create or clobber the default agent ``main``,
  * ``registry.add()`` — the agent is resolvable on the very next message.

The other half is open-ended and belongs to ``write``: ``[app]``, ``[tools]``, the display keys,
``skills``, and above all ``[plugins.<plugin>.tools.<tool>]`` — any plugin x any tool x any knob
(figure-creator's whole image engine lives there). That has no enumerable shape, so a
parameterised creator could never be complete; it would grow forever and still miss cases, while
teaching the model a second grammar for something it already writes fluently as TOML.

The model can do that safely because it has the grammar (the `build-agent` skill, owned by the
agents/agent-builder/ agent)
and a checker (``validate_agent``). Anything this tool cannot express, it names in its result so
the next step is obvious.
"""

from __future__ import annotations

import re
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.write_scope import WriteRefused, check_write


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")


def _toml_str(s: str) -> str:
    """A minimally-escaped TOML basic string (backslash + double-quote)."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _toml_arr(items: list) -> str:
    """A TOML array of basic strings, e.g. ["check-*", "lint"]."""
    return "[" + ", ".join(_toml_str(i) for i in items) + "]"


# What this tool writes. Anything else in an agent.toml was authored with `write`, and a
# re-scaffold silently deletes it — so it has to be named before that happens.
_SKELETON_KEYS = frozenset({"name", "version", "model", "description", "heartbeat"})
_SKELETON_TABLES = frozenset({"capabilities", "subagents"})


def _authored_sections(path: Path) -> list[str]:
    """Everything in an existing agent.toml that this tool does NOT own, named the way the
    author wrote it (``[app]``, ``[tools]``, ``tagline``, ``[plugins.figures...]``).

    Unreadable or absent file -> empty: a caller cannot be warned about content nobody can
    read, and guessing would be worse than saying nothing."""
    try:
        import tomllib

        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out: list[str] = []
    for key, value in data.items():
        if key in _SKELETON_KEYS or key in _SKELETON_TABLES:
            continue
        if key == "plugins" and isinstance(value, dict):
            out += [f"[plugins.{sub}...]" for sub in sorted(value)]
        elif isinstance(value, dict):
            out.append(f"[{key}]")
        else:
            out.append(key)
    return sorted(out)


class CreateAgentTool(Tool):
    name = "create_agent"
    label = "Create Agent"
    default_retryable = False  # side-effecting (writes a definition); never auto-retry
    description = (
        "SCAFFOLD a new persistent AGENT — an agent is a directory of instructions. Call this "
        "FIRST when asked to build an agent: it writes agents/<id>/ (agent.toml + IDENTITY.md, "
        "plus AGENTS.md when you give rules) and registers it LIVE, so the agent is usable on the "
        "next message with no restart. action='list' shows current agents. "
        "IF THE ID ALREADY EXISTS THIS REFUSES — ask the USER whether to work on that agent or "
        "make a new one, and never decide it yourself. To edit an existing agent, change the "
        "specific files with `write`; action='update' RE-SCAFFOLDS from scratch and destroys "
        "[app], [tools] and plugin wiring, so it needs confirm_overwrite=true and only after the "
        "user has said they want it rebuilt. "
        "Provide a kebab-case `id`, a display `name`, an `identity` (who it "
        "is — role, tone, boundaries), and a `version` (e.g. '1.0.0'); optionally `rules` "
        "(operating do/don'ts), a `model`, a one-line `description`, and `subagents_allow` "
        "(ids/globs it may delegate to). For an AUTONOMOUS agent add `heartbeat` (e.g. '30m') + "
        "`heartbeat_md` and `capabilities` (e.g. {\"autonomy\": true}). "
        "THIS TOOL ONLY WRITES THE SKELETON. Everything else is authored with `write` against the "
        "build-agent skill's grammar: the [app] table and ui/, [tools] allow/deny, the top-level "
        "display keys (tagline/color/suggestions), per-agent tool wiring "
        "([plugins.<plugin>.tools.<tool>]), and its skills/ — write those as "
        "skills/<name>/SKILL.md files, NOT with skill_workshop (that only ever writes into the "
        "CALLING agent's own skills). Use create_tool(agent=<id>) for the agent's private tools. "
        "When the files are written, call validate_agent, then reload_agent. Cannot create or "
        "overwrite the default agent 'main'."
    )
    # `action` is NOT required: execute() defaults it to "create", and validate_args runs the
    # schema BEFORE execute — so requiring it made that default unreachable and rejected the
    # most natural call there is, create_agent(id=..., identity=...). Creating is the
    # overwhelmingly common case; make the common case the one you can omit.
    parameters = {
        "type": "object",
        "required": [],
        "properties": {
            "confirm_overwrite": {
                "type": "boolean",
                "description": "REQUIRED for action='update'. Set true ONLY after the user has "
                "explicitly said to rebuild this agent from scratch — it destroys [app], "
                "[tools], display keys and plugin wiring. Never set it on your own initiative",
            },
            "action": {
                "type": "string",
                "enum": ["create", "update", "list"],
                "description": "create (the default) | update an existing agent | list agents",
            },
            "id": {"type": "string", "description": "agent id, kebab-case (e.g. support-bot)"},
            "name": {"type": "string", "description": "display name (defaults to the id)"},
            "version": {
                "type": "string",
                "description": "agent version, e.g. '1.0.0' (defaults to 1.0.0). Bump it on every "
                "shipped change — bundle installs supersede an older copy BY VERSION",
            },
            "identity": {
                "type": "string",
                "description": "IDENTITY.md body: who the agent is, its tone and boundaries",
            },
            "rules": {
                "type": "string",
                "description": "optional AGENTS.md body: operating rules / red lines",
            },
            "model": {"type": "string", "description": "optional model id override for this agent"},
            "description": {
                "type": "string",
                "description": "one line: what this agent is for (shown in agents_list)",
            },
            "subagents_allow": {
                "type": "array",
                "items": {"type": "string"},
                "description": "ids/globs of specialist agents this one may delegate to "
                "(e.g. ['check-*']); omit for no restriction",
            },
            "heartbeat": {
                "type": "string",
                "description": "self-wake interval, e.g. '30m' (needs autonomy enabled)",
            },
            "heartbeat_md": {
                "type": "string",
                "description": "HEARTBEAT.md: the checklist to re-run on each heartbeat tick",
            },
            "capabilities": {
                "type": "object",
                "description": 'per-agent toggles, e.g. {"autonomy": true, "notify": true}',
            },
        },
    }

    def __init__(self, registry):
        self._registry = registry

    async def execute(self, tool_call_id, params, abort, on_update=None):
        action = (params.get("action") or "create").strip().lower()
        reg = self._registry
        if action == "list":
            ids = reg.list_ids()
            return ToolResult.text("Agents:\n" + "\n".join(f"- {i}" for i in ids))
        if action not in ("create", "update"):
            return ToolResult.text("action must be create / update / list", is_error=True)

        agent_id = _slug(params.get("id", ""))
        if not agent_id:
            return ToolResult.text("an agent needs an 'id' (kebab-case)", is_error=True)
        if agent_id == "main":
            return ToolResult.text(
                "'main' is the default agent and cannot be created or overwritten", is_error=True
            )
        identity = (params.get("identity") or "").strip()
        if not identity:
            return ToolResult.text(
                "an agent needs an 'identity' (who it is — its role, tone, and boundaries)",
                is_error=True,
            )
        name = (params.get("name") or agent_id).strip()
        # Default rather than omit: an agent with no version cannot be superseded by a later
        # install (bundles compare versions), and validate_agent flags its absence.
        version = (params.get("version") or "1.0.0").strip()
        model = (params.get("model") or "").strip()
        rules = (params.get("rules") or "").strip()
        description = (params.get("description") or "").strip()
        raw_allow = params.get("subagents_allow")
        allow = (
            [str(a).strip() for a in raw_allow if str(a).strip()]
            if isinstance(raw_allow, list)
            else []
        )
        heartbeat = (params.get("heartbeat") or "").strip()
        heartbeat_md = (params.get("heartbeat_md") or "").strip()
        caps = params.get("capabilities") if isinstance(params.get("capabilities"), dict) else {}

        d = Path(reg.agents_dir) / agent_id
        existed = d.is_dir()
        if existed and action == "create":
            # This message used to read "use action='update' to change it" — a one-step
            # workaround the model took on its own, which is how an existing agent lost its
            # [app] table and orphaned a whole ui/ folder. The refusal must hand the decision
            # BACK to the user, and offer no shortcut.
            return ToolResult.text(
                f"agent '{agent_id}' already exists — this is the user's decision, not yours.\n"
                f"ASK THEM: work on the existing '{agent_id}', or create a new agent?\n"
                f"  • new agent      -> call again with a DIFFERENT id\n"
                f"  • edit this one  -> use `write` on the specific files; everything else "
                f"stays intact\n"
                f"  • rebuild it     -> only if they explicitly ask for it from scratch: "
                f"action='update' with confirm_overwrite=true",
                is_error=True,
            )
        if not existed and action == "update":
            return ToolResult.text(
                f"no agent '{agent_id}' to update — use action='create'", is_error=True
            )

        # An update REWRITES agent.toml from the skeleton, so anything `write` authored is
        # gone. Name it before doing it, and refuse without explicit confirmation.
        destroyed: list[str] = []
        if existed and action == "update":
            destroyed = _authored_sections(d / "agent.toml")
            if not params.get("confirm_overwrite"):
                lost = ", ".join(destroyed) if destroyed else "nothing beyond the skeleton"
                return ToolResult.text(
                    f"REFUSING to rebuild '{agent_id}' — this would DELETE: {lost}.\n"
                    f"Ask the user first. If they only want a change, use `write` on the "
                    f"specific file instead — it keeps everything else.\n"
                    f"If they genuinely want it rebuilt from scratch, call again with "
                    f"confirm_overwrite=true.",
                    is_error=True,
                )

        # This tool writes files directly, so it carries the same scope `write` does.
        try:
            check_write(d)
        except WriteRefused as e:
            return ToolResult.text(str(e), is_error=True)

        d.mkdir(parents=True, exist_ok=True)
        # TOML: top-level keys MUST precede any [table], so emit name/model/description/heartbeat
        # first, then the [capabilities] / [subagents] tables.
        top = [f"name = {_toml_str(name)}", f"version = {_toml_str(version)}"]
        if model:
            top.append(f"model = {_toml_str(model)}")
        if description:
            top.append(f"description = {_toml_str(description)}")
        if heartbeat:
            top.append(f"heartbeat = {_toml_str(heartbeat)}")
        tables: list = []
        cap_lines = [
            f"{k} = {'true' if caps.get(k) else 'false'}"
            for k in ("autonomy", "notify", "channels")
            if k in caps
        ]
        if cap_lines:
            tables += ["", "[capabilities]"] + cap_lines
        if allow:
            tables += ["", "[subagents]", f"allow = {_toml_arr(allow)}"]
        (d / "agent.toml").write_text("\n".join(top + tables) + "\n", encoding="utf-8")
        (d / "IDENTITY.md").write_text(identity + "\n", encoding="utf-8")
        if rules:
            (d / "AGENTS.md").write_text(rules + "\n", encoding="utf-8")
        if heartbeat_md:
            (d / "HEARTBEAT.md").write_text(heartbeat_md + "\n", encoding="utf-8")

        try:
            reg.add(agent_id)  # register LIVE — resolvable next message, no restart
        except Exception as e:  # noqa: BLE001 — files are written; report the registration failure
            return ToolResult.text(
                f"wrote agents/{agent_id}/ but could not register it live "
                f"({type(e).__name__}: {e}) — a restart will pick it up",
                is_error=True,
            )

        verb = "Rebuilt" if existed else "Created"
        written = ["agent.toml", "IDENTITY.md"]
        if rules:
            written.append("AGENTS.md")
        if heartbeat_md:
            written.append("HEARTBEAT.md")

        # HAND-OFF: this tool only ever writes the skeleton, so say so and name the next step.
        # The model's whole picture of a tool is its description + its results, and this is the
        # moment the remaining work is actionable.
        lines = [
            f"{verb} agent '{agent_id}' ({name}) v{version} at {d} — registered live, resolvable "
            f"on the next message, no restart.",
            f"Wrote: {', '.join(written)}.",
        ]
        if destroyed:
            # Say what was destroyed, in the same message, every time. If this rebuild happened
            # without the user actually asking for it, this line is how they find out now
            # instead of when the app window stops opening.
            lines += [
                f"DELETED (re-scaffold replaced agent.toml): {', '.join(destroyed)}.",
                "Re-author anything still needed with `write`.",
            ]
        lines += [
            "",
            "This is the SKELETON only. Author anything else with `write` (see the build-agent "
            "skill for the grammar):",
            "  • [app] + ui/  — to give it its own window / make it packageable as an .exe",
            "  • [tools] allow/deny  — to narrow what it may use",
            "  • tagline / color / suggestions  — TOP-LEVEL keys, never inside [app]",
            "  • [plugins.<plugin>.tools.<tool>]  — per-agent model + tool wiring",
            "  • skills/<name>/SKILL.md  — its playbooks. Author these with `write`: "
            "skill_workshop only ever writes into the CALLING agent's own skills dir.",
            "Then: create_tool(agent='%s') for its private tools." % agent_id,
            f"Finally: validate_agent('{agent_id}') to check it, then reload_agent('{agent_id}') "
            f"to apply any agent.toml edits.",
        ]
        return ToolResult.text(
            "\n".join(lines),
            details={
                "id": agent_id,
                "version": version,
                "written": written,
                "destroyed": destroyed,
                "scope": "skeleton",
            },
        )

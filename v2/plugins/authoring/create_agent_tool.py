"""create_agent — author a new persistent AGENT by chatting.

"An agent is a directory": this writes ``agents/<id>/`` (``agent.toml`` + ``IDENTITY.md``, plus
``AGENTS.md`` when operating rules are given), then registers it in the LIVE registry via
``registry.add(id)`` — the inverse of the registry's ``remove()`` — so the new agent is
resolvable on the next message WITHOUT a restart. The files it writes are exactly what
``FileAgentRegistry`` / ``load_bootstrap`` read, so a by-chat agent is identical to a
hand-authored one.
"""

from __future__ import annotations

import re
from pathlib import Path

from agentd.application.interfaces.tool import Tool, ToolResult


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")


def _toml_str(s: str) -> str:
    """A minimally-escaped TOML basic string (backslash + double-quote)."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _toml_arr(items: list) -> str:
    """A TOML array of basic strings, e.g. ["check-*", "lint"]."""
    return "[" + ", ".join(_toml_str(i) for i in items) + "]"


class CreateAgentTool(Tool):
    name = "create_agent"
    label = "Create Agent"
    default_retryable = False        # side-effecting (writes a definition); never auto-retry
    description = (
        "Create a new persistent AGENT by chatting — an agent is a directory of instructions. "
        "action='create' writes agents/<id>/ (agent.toml + IDENTITY.md, plus AGENTS.md when you "
        "give rules) and registers it LIVE so it is usable on the next message with no restart; "
        "action='update' rewrites an existing agent; action='list' shows current agents. Provide "
        "a short kebab-case `id`, a display `name`, and an `identity` (who the agent is — its "
        "role, tone, and boundaries); optionally `rules` (operating do/don'ts), a `model`, a "
        "one-line `description` (what it's for — shown to orchestrators picking who to delegate "
        "to), and `subagents_allow` (ids/globs of specialist agents THIS agent may delegate to, "
        "e.g. ['check-*']). For an AUTONOMOUS agent, set `heartbeat` (e.g. '30m') + `heartbeat_md` "
        "(its standing checklist) and `capabilities` (e.g. {\"autonomy\": true}) — it self-wakes "
        "live, no restart. Cannot create or overwrite the default agent 'main'."
    )
    parameters = {
        "type": "object",
        "required": ["action"],
        "properties": {
            "action": {"type": "string", "enum": ["create", "update", "list"]},
            "id": {"type": "string", "description": "agent id, kebab-case (e.g. support-bot)"},
            "name": {"type": "string", "description": "display name (defaults to the id)"},
            "identity": {"type": "string",
                         "description": "IDENTITY.md body: who the agent is, its tone and boundaries"},
            "rules": {"type": "string",
                      "description": "optional AGENTS.md body: operating rules / red lines"},
            "model": {"type": "string", "description": "optional model id override for this agent"},
            "description": {"type": "string",
                            "description": "one line: what this agent is for (shown in agents_list)"},
            "subagents_allow": {
                "type": "array", "items": {"type": "string"},
                "description": "ids/globs of specialist agents this one may delegate to "
                               "(e.g. ['check-*']); omit for no restriction"},
            "heartbeat": {"type": "string",
                          "description": "self-wake interval, e.g. '30m' (needs autonomy enabled)"},
            "heartbeat_md": {"type": "string",
                             "description": "HEARTBEAT.md: the checklist to re-run on each heartbeat tick"},
            "capabilities": {"type": "object",
                             "description": "per-agent toggles, e.g. {\"autonomy\": true, \"notify\": true}"},
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
                "'main' is the default agent and cannot be created or overwritten", is_error=True)
        identity = (params.get("identity") or "").strip()
        if not identity:
            return ToolResult.text(
                "an agent needs an 'identity' (who it is — its role, tone, and boundaries)",
                is_error=True)
        name = (params.get("name") or agent_id).strip()
        model = (params.get("model") or "").strip()
        rules = (params.get("rules") or "").strip()
        description = (params.get("description") or "").strip()
        raw_allow = params.get("subagents_allow")
        allow = [str(a).strip() for a in raw_allow if str(a).strip()] \
            if isinstance(raw_allow, list) else []
        heartbeat = (params.get("heartbeat") or "").strip()
        heartbeat_md = (params.get("heartbeat_md") or "").strip()
        caps = params.get("capabilities") if isinstance(params.get("capabilities"), dict) else {}

        d = Path(reg.agents_dir) / agent_id
        existed = d.is_dir()
        if existed and action == "create":
            return ToolResult.text(
                f"agent '{agent_id}' already exists — use action='update' to change it",
                is_error=True)
        if not existed and action == "update":
            return ToolResult.text(
                f"no agent '{agent_id}' to update — use action='create'", is_error=True)

        d.mkdir(parents=True, exist_ok=True)
        # TOML: top-level keys MUST precede any [table], so emit name/model/description/heartbeat
        # first, then the [capabilities] / [subagents] tables.
        top = [f"name = {_toml_str(name)}"]
        if model:
            top.append(f"model = {_toml_str(model)}")
        if description:
            top.append(f"description = {_toml_str(description)}")
        if heartbeat:
            top.append(f"heartbeat = {_toml_str(heartbeat)}")
        tables: list = []
        cap_lines = [f"{k} = {'true' if caps.get(k) else 'false'}"
                     for k in ("autonomy", "notify", "channels") if k in caps]
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
            reg.add(agent_id)               # register LIVE — resolvable next message, no restart
        except Exception as e:  # noqa: BLE001 — files are written; report the registration failure
            return ToolResult.text(
                f"wrote agents/{agent_id}/ but could not register it live "
                f"({type(e).__name__}: {e}) — a restart will pick it up", is_error=True)

        verb = "Updated" if existed else "Created"
        return ToolResult.text(
            f"{verb} agent '{agent_id}' ({name}) at {d} and registered it live — it's resolvable "
            f"now, no restart needed.", details={"id": agent_id})

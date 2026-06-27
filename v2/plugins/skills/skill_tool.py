"""skill_workshop — the agent captures a reusable procedure as a SKILL.md at runtime (S10).

Writes the skill into the CALLING AGENT'S OWN skills dir (`agents/<id>/skills/<name>/SKILL.md`)
so a self-authored skill is per-agent and overrides a global one of the same name. The default
`main` agent's skills ARE the shared/global library (agents/main/skills/), so when main authors
a skill it becomes available to EVERY agent — one-way: a named agent's skills stay private to it.
Skills are re-read fresh each turn, so a new/edited skill is available on the NEXT turn.
"""

from __future__ import annotations

import re
from pathlib import Path

from agentd.application.run_context import current_run_context

from agentd.application.interfaces.tool import Tool, ToolResult


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")


class SkillWorkshopTool(Tool):
    name = "skill_workshop"
    label = "Skill"
    description = (
        "Capture a reusable procedure as a SKILL.md so you (and future sessions) can apply it "
        "later. action='create'/'update' writes the skill (give name + a one-line description "
        "of WHEN to use it + a step-by-step body); action='list' shows existing skills. The new "
        "skill is available next turn.")
    parameters = {
        "type": "object", "required": ["action"],
        "properties": {
            "action": {"type": "string", "enum": ["create", "update", "list"]},
            "name": {"type": "string", "description": "skill id, kebab-case (e.g. suumo-deal-watch)"},
            "description": {"type": "string", "description": "one line: WHEN to use this skill"},
            "body": {"type": "string", "description": "the step-by-step playbook (markdown)"},
        },
    }

    def __init__(self, config):
        self._config = config

    def _target_dir(self) -> Path:
        """The calling agent's OWN agents/<id>/skills/ — main's IS the shared/global library.
        Tied to the agent's definition dir (agents_dir/<id>/skills), matching the registry, so
        it's correct even when the agent's workspace is relocated elsewhere."""
        ctx = current_run_context()
        agent_id = (ctx.agent_id if ctx else "") or "main"
        agents_dir = getattr(self._config, "agents_dir", None)
        if agents_dir:
            return Path(agents_dir) / agent_id / "skills"   # agents/<id>/skills/ (main's = global)
        return Path(getattr(self._config, "skills_dir", "skills"))   # fallback -> global library

    async def execute(self, tool_call_id, params, abort, on_update=None):
        target = self._target_dir()
        action = (params.get("action") or "").strip().lower()
        if action == "list":
            names = sorted(p.parent.name for p in target.glob("*/SKILL.md")) \
                if target.exists() else []
            return ToolResult.text("Skills:\n" + "\n".join(f"- {n}" for n in names)
                                   if names else "(no skills yet)")
        if action not in ("create", "update"):
            return ToolResult.text("action must be create / update / list", is_error=True)
        name = _slug(params.get("name", ""))
        if not name:
            return ToolResult.text("a skill needs a 'name'", is_error=True)
        body = (params.get("body") or "").strip()
        desc = (params.get("description") or "").strip()
        if not body:
            return ToolResult.text("a skill needs a 'body' (the playbook)", is_error=True)
        d = target / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {desc}\n---\n\n{body}\n", encoding="utf-8")
        return ToolResult.text(f"skill '{name}' saved ({d / 'SKILL.md'}) — available next turn.",
                               details={"name": name})

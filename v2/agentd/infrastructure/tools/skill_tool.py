"""skill_workshop — the agent captures a reusable procedure as a SKILL.md at runtime (S10).

Writes `<skills_dir>/<name>/SKILL.md` (frontmatter + playbook). FileSkillRegistry reads skills
fresh each turn, so a new/edited skill is available on the NEXT turn (hot-reload). This is how
the agent turns "how I did X" into a repeatable, deployable skill.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import Tool, ToolResult


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
        self._dir = Path(getattr(config, "skills_dir", "skills"))

    async def execute(self, tool_call_id, params, abort, on_update=None):
        action = (params.get("action") or "").strip().lower()
        if action == "list":
            names = sorted(p.parent.name for p in self._dir.glob("*/SKILL.md")) \
                if self._dir.exists() else []
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
        d = self._dir / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {desc}\n---\n\n{body}\n", encoding="utf-8")
        return ToolResult.text(f"skill '{name}' saved ({d / 'SKILL.md'}) — available next turn.",
                               details={"name": name})

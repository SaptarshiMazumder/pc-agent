"""Built-in 'skills' bundle — skill_workshop: author reusable SKILL.md playbooks at runtime.

Gating ported from build_tools: registers only when ``skill_workshop`` is enabled. OFF => absent.
(NB: this is the workshop TOOL; skill discovery/advertisement itself is core, in resolve_skills.)
"""

from __future__ import annotations


def register(api, ctx):
    if not getattr(ctx.config, "skill_workshop", False):
        return
    from skill_tool import SkillWorkshopTool

    api.register_tool(SkillWorkshopTool(ctx.config))

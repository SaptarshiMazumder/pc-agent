"""Skill + SkillRegistry — the contract for loadable know-how playbooks.

A *skill* is NOT a callable action (that's a tool). It is a markdown instruction
file (`SKILL.md`) that teaches the agent HOW to do a specific task well. The agent
reads the full file on demand (via the ordinary `read` tool) only when a task
matches the skill's description — so the system prompt carries just a one-line
summary per skill (progressive disclosure), not the whole playbook.

This is the same `SKILL.md` pattern OpenClaw and Anthropic's Agent Skills use:
capability/know-how scaling without prompt bloat and without hardcoding workflows
into code.

The use-case / prompt builder depends on THIS interface, not on any concrete
loader. A file-backed registry satisfies it today; a future cloud or
per-user-vault registry would satisfy the same interface and slot in by config.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Skill:
    """One discovered skill — just the metadata needed to advertise + locate it.

    The full playbook body is NOT held here; the agent reads it from ``path`` with
    the ``read`` tool when the skill is relevant. That keeps the prompt small no
    matter how many skills exist.
    """

    name: str  # short id, e.g. "photoshop-export" (folder name if unspecified)
    description: str  # one line: WHEN to use this skill (matched against the task)
    path: str  # absolute path to the SKILL.md the agent should read
    always: bool = False  # if true, the full body is INLINED into the prompt every turn
    body: str = ""  # the playbook body (only needed/populated for always-on skills)
    # Gating (OpenClaw `requires`): a skill is HIDDEN unless its deps are present. Keys:
    #   "bins"   -> all must be on PATH       "env" -> all must be set
    #   "config" -> all (top-level) config attrs must be truthy
    # Parsed from frontmatter `requires_bins` / `requires_env` / `requires_config` (CSV).
    requires: dict = field(default_factory=dict)


class SkillRegistry(Protocol):
    """Discover the available skills. Read fresh each turn so newly dropped-in
    skills are picked up without a restart."""

    def all(self) -> list[Skill]: ...

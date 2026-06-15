"""FileSkillRegistry — discover skills from a folder of ``SKILL.md`` files.

Layout (one folder per skill, so a skill can bundle scripts/assets alongside it):

    skills/
      photoshop-export/
        SKILL.md          <- frontmatter + playbook
      linkedin-jobs/
        SKILL.md

Each ``SKILL.md`` starts with simple ``key: value`` frontmatter between ``---``
fences::

    ---
    name: photoshop-export
    description: Use when exporting/automating Adobe Photoshop via its scripting API.
    ---
    # Photoshop Export
    ...the playbook...

Parsing is intentionally dependency-free (no YAML lib): we only read ``name`` and
``description`` from the frontmatter. Missing ``name`` falls back to the folder
name; missing ``description`` falls back to the first markdown heading/line. A
folder without a ``SKILL.md`` is skipped, so the directory can hold a README and
other helpers without polluting the registry.
"""

from __future__ import annotations

import logging
from pathlib import Path

from agentd.application.interfaces.skills import Skill

log = logging.getLogger("agentd")

SKILL_FILE = "SKILL.md"


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Return (frontmatter dict, body). Frontmatter is the ``key: value`` block
    between leading ``---`` fences; absent ⇒ ({}, whole text)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    meta: dict[str, str] = {}
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            body = "\n".join(lines[i + 1 :])
            return meta, body
        key, sep, value = lines[i].partition(":")
        if sep:
            meta[key.strip().lower()] = value.strip()
    # no closing fence — treat as no frontmatter
    return {}, text


def _first_meaningful_line(body: str) -> str:
    for line in body.splitlines():
        stripped = line.lstrip("#").strip()
        if stripped:
            return stripped
    return ""


def _load_one(skill_md: Path) -> Skill | None:
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("skipping unreadable skill %s: %s", skill_md, exc)
        return None
    meta, body = _parse_frontmatter(text)
    name = meta.get("name") or skill_md.parent.name
    description = meta.get("description") or _first_meaningful_line(body)
    return Skill(name=name, description=description, path=str(skill_md.resolve()))


class FileSkillRegistry:
    """Scan ``skills_dir`` for ``*/SKILL.md`` and return their metadata.

    Read fresh on every ``all()`` call so skills dropped into the folder are picked
    up without restarting the gateway. Cheap: a handful of small file reads.
    """

    def __init__(self, skills_dir: Path | str):
        self._dir = Path(skills_dir)

    def all(self) -> list[Skill]:
        if not self._dir.is_dir():
            return []
        skills: list[Skill] = []
        for child in sorted(self._dir.iterdir()):
            if not child.is_dir():
                continue
            skill_md = child / SKILL_FILE
            if not skill_md.is_file():
                continue
            skill = _load_one(skill_md)
            if skill is not None:
                skills.append(skill)
        return skills

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

from agentd.application.descriptions import first_meaningful_line
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


def _csv(value: str) -> list:
    return [x.strip() for x in (value or "").split(",") if x.strip()]


def _load_one(skill_md: Path) -> Skill | None:
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("skipping unreadable skill %s: %s", skill_md, exc)
        return None
    meta, body = _parse_frontmatter(text)
    name = meta.get("name") or skill_md.parent.name
    description = meta.get("description") or first_meaningful_line(body)
    always = meta.get("always", "").strip().lower() in ("true", "1", "yes", "on")
    requires = {
        k: v
        for k, v in (
            ("bins", _csv(meta.get("requires_bins", ""))),
            ("env", _csv(meta.get("requires_env", ""))),
            ("config", _csv(meta.get("requires_config", ""))),
        )
        if v
    }
    return Skill(
        name=name,
        description=description,
        path=str(skill_md.resolve()),
        always=always,
        body=body.strip() if always else "",
        requires=requires,
    )


def skill_eligible(skill, config=None) -> bool:
    """OpenClaw's `requires` gate: a skill is eligible only if its declared deps are present —
    all `bins` on PATH, all `env` vars set, all `config` attrs truthy. No requires => always
    eligible. IO-touching (PATH/env), so kept out of the pure domain layer."""
    req = getattr(skill, "requires", None) or {}
    if not req:
        return True
    import os
    import shutil

    if any(shutil.which(b) is None for b in req.get("bins", [])):
        return False
    if any(not os.environ.get(e) for e in req.get("env", [])):
        return False
    if any(not getattr(config, c, None) for c in req.get("config", [])):
        return False
    return True


def load_skills_dir(skills_dir: Path | str) -> list[Skill]:
    """Scan ONE folder for ``*/SKILL.md`` -> list[Skill] (empty if the dir is absent).

    The shared loader used for both the global library and an agent's own skills dir.
    """
    d = Path(skills_dir)
    if not d.is_dir():
        return []
    skills: list[Skill] = []
    for child in sorted(d.iterdir()):
        if not child.is_dir():
            continue
        skill_md = child / SKILL_FILE
        if not skill_md.is_file():
            continue
        skill = _load_one(skill_md)
        if skill is not None:
            skills.append(skill)
    return skills


class FileSkillRegistry:
    """Scan ``skills_dir`` for ``*/SKILL.md`` and return their metadata.

    Read fresh on every ``all()`` call so skills dropped into the folder are picked
    up without restarting the gateway. Cheap: a handful of small file reads.
    """

    def __init__(self, skills_dir: Path | str):
        self._dir = Path(skills_dir)

    def all(self) -> list[Skill]:
        return load_skills_dir(self._dir)

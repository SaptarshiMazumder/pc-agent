import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.infrastructure.skills.file_skills import (
    FileSkillRegistry,
    _parse_frontmatter,
)
from agentd.infrastructure.prompt import _skills_section
from agentd.application.interfaces.skills import Skill


def _make_skill(root: Path, folder: str, text: str) -> Path:
    d = root / folder
    d.mkdir(parents=True)
    md = d / "SKILL.md"
    md.write_text(text, encoding="utf-8")
    return md


def test_parse_frontmatter():
    meta, body = _parse_frontmatter(
        "---\nname: foo\ndescription: do a thing\n---\n# Title\nbody\n"
    )
    assert meta == {"name": "foo", "description": "do a thing"}
    assert body.startswith("# Title")


def test_parse_frontmatter_absent():
    meta, body = _parse_frontmatter("# Just a heading\ntext")
    assert meta == {}
    assert body == "# Just a heading\ntext"


def test_registry_reads_frontmatter(tmp_path):
    _make_skill(
        tmp_path,
        "sample-skill",
        "---\nname: sample-skill\ndescription: Use for the sample task.\n---\n# Sample\nsteps\n",
    )
    skills = FileSkillRegistry(tmp_path).all()
    assert len(skills) == 1
    s = skills[0]
    assert s.name == "sample-skill"
    assert s.description == "Use for the sample task."
    assert s.path.endswith("SKILL.md")
    assert Path(s.path).is_file()


def test_name_falls_back_to_folder_and_desc_to_first_line(tmp_path):
    _make_skill(tmp_path, "my-skill", "# My Skill\nWhat it does.\n")
    s = FileSkillRegistry(tmp_path).all()[0]
    assert s.name == "my-skill"            # no frontmatter name -> folder
    assert s.description == "My Skill"     # first meaningful line


def test_folders_without_skill_md_and_loose_files_ignored(tmp_path):
    _make_skill(tmp_path, "real", "---\ndescription: real one\n---\nbody")
    (tmp_path / "notes").mkdir()                       # dir, no SKILL.md
    (tmp_path / "README.md").write_text("x", encoding="utf-8")  # loose file
    skills = FileSkillRegistry(tmp_path).all()
    assert [s.name for s in skills] == ["real"]


def test_missing_dir_returns_empty(tmp_path):
    assert FileSkillRegistry(tmp_path / "nope").all() == []


def test_skills_sorted_deterministically(tmp_path):
    _make_skill(tmp_path, "bbb", "---\ndescription: b\n---\n")
    _make_skill(tmp_path, "aaa", "---\ndescription: a\n---\n")
    names = [s.name for s in FileSkillRegistry(tmp_path).all()]
    assert names == ["aaa", "bbb"]


def test_prompt_section_omitted_when_no_skills():
    assert _skills_section([]) is None
    assert _skills_section(None) is None


def test_prompt_section_lists_each_skill():
    section = _skills_section(
        [Skill(name="sample-skill", description="does the sample", path="/s/x/SKILL.md")]
    )
    assert "## Skills" in section
    assert "sample-skill: does the sample" in section
    assert "[read: /s/x/SKILL.md]" in section

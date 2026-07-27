"""Skill gating (OpenClaw `requires`) + prompt budget — a big library never floods the prompt."""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.application.interfaces.skills import Skill
from agentd.infrastructure.prompt import _advertise_skills
from agentd.infrastructure.skills.file_skills import load_skills_dir, skill_eligible

# ── requires gate ────────────────────────────────────────────────────────────


def _skill(**requires):
    return Skill(name="s", description="d", path="/x", requires=requires)


def test_no_requires_is_eligible():
    assert skill_eligible(Skill(name="s", description="d", path="/x"))


def test_missing_bin_hides_skill():
    assert not skill_eligible(_skill(bins=["definitely_absent_bin_zzz_123"]))


def test_env_gate(monkeypatch):
    monkeypatch.delenv("AGENTD_TEST_SKILLVAR", raising=False)
    assert not skill_eligible(_skill(env=["AGENTD_TEST_SKILLVAR"]))
    monkeypatch.setenv("AGENTD_TEST_SKILLVAR", "1")
    assert skill_eligible(_skill(env=["AGENTD_TEST_SKILLVAR"]))


def test_config_gate():
    assert skill_eligible(_skill(config=["model"]), SimpleNamespace(model="x"))
    assert not skill_eligible(_skill(config=["model"]), SimpleNamespace(model=""))
    assert not skill_eligible(_skill(config=["nope"]), SimpleNamespace())


def test_loader_parses_requires(tmp_path):
    d = tmp_path / "gemini"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: gemini\ndescription: x\nrequires_bins: gemini, ffmpeg\nrequires_env: TOK\n---\nbody",
        encoding="utf-8",
    )
    sk = load_skills_dir(tmp_path)[0]
    assert sk.requires == {"bins": ["gemini", "ffmpeg"], "env": ["TOK"]}


# ── prompt budget ────────────────────────────────────────────────────────────


def _sk(i):
    return Skill(
        name=f"skill{i}", description="a fairly wordy description here", path=f"/p/s{i}/SKILL.md"
    )


def test_full_format_when_under_budget():
    lines = _advertise_skills([_sk(i) for i in range(5)], 150, 18000)
    assert (
        len(lines) == 5 and "a fairly wordy description here" in lines[0]
    )  # full, with descriptions


def test_compact_and_truncate_when_over_char_budget():
    lines = _advertise_skills([_sk(i) for i in range(50)], 150, 400)  # tiny char budget
    body = [l for l in lines if "more skills" not in l]
    assert all("a fairly wordy description here" not in l for l in body)  # compact: no descriptions
    assert any("more skills" in l for l in lines)  # truncated with a +N note


def test_count_cap_forces_compact():
    lines = _advertise_skills([_sk(i) for i in range(50)], 10, 1_000_000)  # count cap 10
    body = [l for l in lines if "more skills" not in l]
    assert len(body) <= 10 and all(": a fairly" not in l for l in body)  # capped + compact


def test_skills_section_applies_budget():
    skills = [_sk(i) for i in range(40)]
    cfg = SimpleNamespace(skills_prompt_max=150, skills_prompt_chars=300)
    p = build_skills(skills, cfg)
    assert (
        "more skills" in p and "a fairly wordy description here" not in p
    )  # degraded under budget


def build_skills(skills, cfg):
    from agentd.infrastructure.prompt import _skills_section

    return _skills_section(skills, cfg) or ""

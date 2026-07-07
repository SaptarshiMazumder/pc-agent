"""agents.detail — the Agent DETAIL page's data: workspace file listing + skills (shared + own)."""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _skill(dir_: Path, name: str, desc: str) -> None:
    d = dir_ / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\nbody\n", encoding="utf-8")


def test_agents_detail_lists_workspace_and_skills(tmp_path):
    from agentd.presentation.gateway import Gateway

    # main: shared skills library + its own workspace
    main_ws = tmp_path / "agents" / "main" / "workspace"
    main_skills = tmp_path / "agents" / "main" / "skills"
    main_ws.mkdir(parents=True)
    _skill(main_skills, "shared-skill", "shared library skill")

    # support: its own workspace (with a file) + its own skill
    sup_ws = tmp_path / "agents" / "support" / "workspace"
    sup_skills = tmp_path / "agents" / "support" / "skills"
    sup_ws.mkdir(parents=True)
    (sup_ws / "report.png").write_bytes(b"\x89PNG\r\n")
    (sup_ws / "notes.txt").write_text("hi", encoding="utf-8")
    _skill(sup_skills, "support-skill", "answer tickets")

    specs = {
        "main": SimpleNamespace(id="main", name="JARVIS", tagline="general", version="1",
                                model=None, color="#a3e635", workspace=main_ws, skills_dir=main_skills),
        "support": SimpleNamespace(id="support", name="Support", tagline="tickets", version="2",
                                   model="gemini/x", color="#3366cc", workspace=sup_ws, skills_dir=sup_skills,
                                   description="help desk"),
    }
    gw = Gateway(
        config=SimpleNamespace(state_dir=tmp_path, agent_id="main"),
        service=None,
        registry=SimpleNamespace(list_ids=lambda: list(specs), get=lambda a: specs[a]),
    )

    out = gw._agents_detail({"agentId": "support"})
    assert out["id"] == "support" and out["name"] == "Support" and out["version"] == "2"
    assert out["model"] == "gemini/x"

    files = {f["name"]: f["kind"] for f in out["workspaceFiles"]}
    assert files == {"report.png": "image", "notes.txt": "file"}

    skills = {s["name"] for s in out["skills"]}
    assert skills == {"shared-skill", "support-skill"}   # shared library + the agent's own


def test_agents_detail_unknown_agent(tmp_path):
    from agentd.presentation.gateway import Gateway

    gw = Gateway(
        config=SimpleNamespace(state_dir=tmp_path, agent_id="main"),
        service=None,
        registry=SimpleNamespace(list_ids=lambda: ["main"], get=_raise_keyerror),
    )
    out = gw._agents_detail({"agentId": "ghost"})
    assert out["workspaceFiles"] == [] and out["skills"] == []


def _raise_keyerror(_a):
    raise KeyError(_a)

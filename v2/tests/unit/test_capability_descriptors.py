"""Uniform capability descriptions + descriptors + the delegation roster.

Covers the "everything self-describes, uniformly" change: the shared first-line fallback, the
agent/plugin description resolvers, the CapabilityDescriptor producers, and the model-facing
agent roster (gated on holding a delegation tool + honoring the allowlist).
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_runtime.application import capabilities as cap
from agent_runtime.application.descriptions import first_meaningful_line


# ---- the shared fallback helper --------------------------------------------------
def test_first_line_keeps_skill_heading_but_skips_agent_heading():
    body = "# Figure Creator\n\n- You are a studio for figures.\nmore\n"
    assert first_meaningful_line(body) == "Figure Creator"  # skills: keep H1 text
    assert first_meaningful_line(body, skip_headings=True) == "You are a studio for figures."


def test_first_line_blank_and_truncation():
    assert first_meaningful_line("") == ""
    assert first_meaningful_line("   \n\n  ") == ""
    out = first_meaningful_line("x" * 500, limit=50)
    assert len(out) == 50 and out.endswith("…")


# ---- agent description resolver (fallback chain) ---------------------------------
def _registry(tmp_path, agents: dict):
    from agent_runtime.infrastructure.agents.file_registry import FileAgentRegistry

    adir = tmp_path / "agents"
    for aid, files in agents.items():
        d = adir / aid
        d.mkdir(parents=True, exist_ok=True)
        for fname, text in files.items():
            (d / fname).write_text(text, encoding="utf-8")
    cfg = SimpleNamespace(
        state_dir=tmp_path / "state",
        agents_dir=adir,
        workspace=tmp_path,
        agent_name="",
        skills_dir=adir / "main" / "skills",
    )
    return FileAgentRegistry(cfg)


def test_agent_description_fallback_chain(tmp_path):
    reg = _registry(
        tmp_path,
        {
            "explicit": {"agent.toml": 'name = "X"\ndescription = "from toml"\n'},
            "viaident": {
                "agent.toml": 'name = "Y"\n',
                "IDENTITY.md": "# Y\n\nYou are Y, a specialist.\n",
            },
            "viabundle": {
                "agent.toml": 'name = "Z"\n',
                "bundle.toml": '[bundle]\ndescription = "from bundle"\n',
            },
        },
    )
    assert reg.get("explicit").description == "from toml"  # explicit field wins
    assert (
        reg.get("viaident").description == "You are Y, a specialist."
    )  # -> IDENTITY.md first prose line
    assert reg.get("viabundle").description == "from bundle"  # -> bundle.toml
    assert reg.get("main").description == "general · all tools"  # synthesized generalist


# ---- CapabilityDescriptor producers ----------------------------------------------
def test_descriptor_producers_shape():
    specs = [
        SimpleNamespace(
            id="a1",
            name="Agent One",
            description="does one thing",
            tagline="t",
            color="",
            model=None,
            dir="/x",
        )
    ]
    (agent,) = cap.agent_descriptors(specs)
    assert (agent.kind, agent.id, agent.description) == ("agent", "a1", "does one thing")

    catalog = {
        "figures": {
            "name": "Figures",
            "description": "figure core",
            "tools": [{"name": "compose_figure_layers", "full_description": "full desc"}],
        }
    }
    (plugin,) = cap.plugin_descriptors(catalog)
    assert (plugin.kind, plugin.id, plugin.description, plugin.extra["tools"]) == (
        "plugin",
        "figures",
        "figure core",
        1,
    )
    (tool,) = cap.tool_descriptors(catalog)
    assert (tool.kind, tool.id, tool.description, tool.source) == (
        "tool",
        "compose_figure_layers",
        "full desc",
        "figures",
    )


# ---- the model-facing roster (gate + allowlist) ----------------------------------
def _service(registry):
    from agent_runtime.application.services.agent_service import AgentService

    svc = object.__new__(AgentService)  # only _registry is used by the roster method
    svc._registry = registry
    return svc


def test_roster_requires_a_delegation_tool(tmp_path):
    reg = _registry(
        tmp_path,
        {"figure-creator": {"agent.toml": 'name = "Figure Creator"\ndescription = "figures"\n'}},
    )
    svc = _service(reg)
    main = reg.get("main")
    assert (
        svc._agents_roster_section(main, [SimpleNamespace(name="read")]) == ""
    )  # no delegation tool
    section = svc._agents_roster_section(main, [SimpleNamespace(name="spawn_subagent")])
    assert "figure-creator" in section and "figures" in section  # roster carries real descriptions
    assert "- main" not in section  # never advertises itself


def test_roster_honors_allowlist(tmp_path):
    reg = _registry(
        tmp_path,
        {
            "figure-creator": {"agent.toml": 'name = "FC"\ndescription = "figures"\n'},
            "presentation-creator": {"agent.toml": 'name = "PC"\ndescription = "decks"\n'},
            "lead": {"agent.toml": 'name = "Lead"\n\n[subagents]\nallow = ["figure-creator"]\n'},
        },
    )
    svc = _service(reg)
    section = svc._agents_roster_section(reg.get("lead"), [SimpleNamespace(name="message_agent")])
    assert "figure-creator" in section
    assert "presentation-creator" not in section  # outside the allowlist -> hidden

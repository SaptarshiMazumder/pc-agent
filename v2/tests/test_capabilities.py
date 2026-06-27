"""S2 — the self-knowledge ("## What you are") section is built DYNAMICALLY from the
available organs (tools + autonomy/channel state): empty for a bare setup, grows as
capabilities appear, and tells the agent to PROPOSE composing them."""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.infrastructure.prompt import build_system_prompt


def _cfg(**over):
    base = dict(agent_name="A", workspace=Path("."), agent_id="main")
    base.update(over)
    return SimpleNamespace(**base)


def _tool(name):
    return SimpleNamespace(name=name, description=name)


def _agent(**over):
    base = dict(name="A", id="a", workspace=Path("."), instructions="")
    base.update(over)
    return SimpleNamespace(**base)


def test_no_capabilities_section_for_bare_setup():
    # autonomy off, no channels, no memory/sub-agent tools -> no organs -> no section, no noise
    p = build_system_prompt(_cfg(autonomy_enabled=False), [_tool("read"), _tool("write")], "m")
    assert "## What you are" not in p


def test_capabilities_appear_with_autonomy():
    cfg = _cfg(autonomy_enabled=True, notify_enabled=True)
    p = build_system_prompt(cfg, [_tool("cron"), _tool("read")], "m")
    assert "## What you are" in p
    assert "Schedule" in p and "Wake yourself" in p and "Reach the user" in p
    assert "PROPOSE how you'd compose these" in p          # the propose-architecture nudge


def test_capabilities_grow_with_channels_and_memory():
    cfg = _cfg(autonomy_enabled=True, notify_enabled=True, channels=[{"type": "email"}])
    p = build_system_prompt(cfg, [_tool("cron"), _tool("memory_search"), _tool("spawn_subagent")], "m")
    assert "Be reached" in p                                # channels configured
    assert "Remember across sessions" in p                 # memory tool present
    assert "Delegate" in p                                  # sub-agent tool present


def test_capabilities_honest_no_autonomy_no_schedule_claim():
    # without autonomy or a cron tool, it must NOT claim it can schedule/wake
    p = build_system_prompt(_cfg(autonomy_enabled=False, channels=[{"type": "email"}],
                                 notify_enabled=True), [_tool("read")], "m")
    assert "Schedule" not in p and "Wake yourself" not in p
    assert "Be reached" in p                                # but it CAN be reached on the channel


# --- per-agent gates: the AgentSpec value overrides the global default --------------

def test_per_agent_gate_opts_in_when_global_off():
    # global autonomy OFF, but THIS agent's definition opts in -> it CAN wake itself
    cfg = _cfg(autonomy_enabled=False, notify_enabled=True)
    p = build_system_prompt(cfg, [_tool("read")], "m", agent=_agent(autonomy_enabled=True))
    assert "Wake yourself" in p


def test_per_agent_gate_opts_out_when_global_on():
    # global autonomy ON, but THIS agent opts OUT -> no wake claim for it
    cfg = _cfg(autonomy_enabled=True, notify_enabled=True)
    p = build_system_prompt(cfg, [_tool("read")], "m", agent=_agent(autonomy_enabled=False))
    assert "Wake yourself" not in p


def test_per_agent_gate_unset_inherits_global():
    # gate absent (None) -> inherit the global default (autonomy on here)
    cfg = _cfg(autonomy_enabled=True, notify_enabled=True)
    p = build_system_prompt(cfg, [_tool("read")], "m", agent=_agent())
    assert "Wake yourself" in p


# (Google-account prompt tests moved to test_google_plugin.py — that block now lives in the
#  google plugin, not the core prompt builder.)


def test_per_agent_channels_gate_opts_in_when_global_none():
    # no global channels, but the agent definition enables being reached -> "Be reached"
    cfg = _cfg(autonomy_enabled=False, notify_enabled=True, channels=[])
    p = build_system_prompt(cfg, [_tool("read")], "m", agent=_agent(channels_enabled=True))
    assert "Be reached" in p

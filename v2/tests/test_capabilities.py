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


def test_google_account_note_when_set():
    p = build_system_prompt(_cfg(), [_tool("read")], "m", agent=_agent(google_account="x@y.com"))
    assert "## Google account" in p and "x@y.com" in p and "user_google_email" in p


def test_no_google_account_note_when_unset():
    p = build_system_prompt(_cfg(), [_tool("read")], "m", agent=_agent())
    assert "## Google account" not in p


def test_google_note_general_guidance_when_google_tools_present_no_account():
    # NO account configured, but a Google MCP tool is present -> general anti-flail guidance
    p = build_system_prompt(_cfg(), [_tool("google__gmail_send")], "m", agent=_agent())
    assert "## Google accounts" in p
    assert "NOT accounts you log in as" in p         # general identity-vs-recipient rule
    assert "user_google_email" in p                  # task-driven: explains how to pick, zero config
    assert "Default to" not in p                     # but no account is pinned


def test_google_account_global_default_applies():
    # set once globally -> every agent uses it, no per-agent toml needed
    p = build_system_prompt(_cfg(google_account="g@x.com"), [_tool("read")], "m", agent=_agent())
    assert "## Google account" in p and "g@x.com" in p


def test_per_agent_google_account_overrides_global():
    p = build_system_prompt(_cfg(google_account="global@x.com"), [_tool("read")], "m",
                            agent=_agent(google_account="agent@x.com"))
    assert "agent@x.com" in p and "global@x.com" not in p


def test_google_multi_account_guidance():
    # an agent that may use SEVERAL accounts -> told to pick per resource via user_google_email
    p = build_system_prompt(_cfg(), [_tool("google__gmail_send")], "m",
                            agent=_agent(google_accounts=("a@x.com", "b@x.com")))
    assert "a@x.com" in p and "b@x.com" in p
    assert "user_google_email" in p and "owns each resource" in p


def test_per_agent_channels_gate_opts_in_when_global_none():
    # no global channels, but the agent definition enables being reached -> "Be reached"
    cfg = _cfg(autonomy_enabled=False, notify_enabled=True, channels=[])
    p = build_system_prompt(cfg, [_tool("read")], "m", agent=_agent(channels_enabled=True))
    assert "Be reached" in p

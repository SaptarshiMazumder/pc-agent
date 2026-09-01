"""S1 — the default persona is baked into the base prompt (every agent + plain chat),
config-gated, and an agent's IDENTITY is loaded right after so it can refine the tone."""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_runtime.infrastructure.prompt import PERSONA, build_system_prompt


def _cfg(**over):
    base = dict(agent_name="A", workspace=Path("."), agent_id="main")
    base.update(over)
    return SimpleNamespace(**base)


def test_persona_present_by_default():
    p = build_system_prompt(_cfg(), [], "m")
    assert PERSONA in p
    assert "NEVER fabricate" in p and "propose your approach and confirm" in p


def test_persona_disabled_by_config():
    p = build_system_prompt(_cfg(persona_enabled=False), [], "m")
    assert PERSONA not in p
    assert (
        "performative" not in p
    )  # persona-unique phrase (honesty section still mentions fabricate)


def test_persona_precedes_agent_identity():
    # the agent's IDENTITY is loaded AFTER the persona so it can refine/override the tone.
    agent = SimpleNamespace(
        name="Scout", workspace=Path("."), id="scout", instructions="## Scout\nYou are terse."
    )
    p = build_system_prompt(_cfg(), [], "m", agent=agent)
    assert p.index(PERSONA) < p.index("You are terse.")


def test_persona_loaded_from_editable_file(tmp_path):
    # an editable SOUL.md (persona_file) REPLACES the built-in constant.
    soul = tmp_path / "SOUL.md"
    soul.write_text("## How you work\n- Custom soul rule XYZ.", encoding="utf-8")
    p = build_system_prompt(_cfg(persona_file=str(soul)), [], "m")
    assert "Custom soul rule XYZ" in p and PERSONA not in p


def test_persona_falls_back_to_constant_when_file_missing():
    p = build_system_prompt(_cfg(persona_file=str(Path("no") / "such" / "SOUL.md")), [], "m")
    assert PERSONA in p  # missing file -> built-in fallback (never breaks)


# ---- S3: honesty self-check on by default -----------------------------------


def test_honesty_check_on_by_default():
    p = build_system_prompt(_cfg(), [], "m")
    assert "## Before You Finish" in p and "fabricate" in p


def test_honesty_check_can_be_disabled():
    p = build_system_prompt(_cfg(completeness_check=False), [], "m")
    assert "## Before You Finish" not in p

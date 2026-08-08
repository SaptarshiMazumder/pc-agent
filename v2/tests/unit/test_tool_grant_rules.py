"""Half a tool pair is worse than neither half.

`exec(background=true)` hands back a session id and nothing else; `process` is the only way to
poll it, read its output, or kill it. `exec`'s own description tells the model to use
`process`. Grant one without the other and the model is pointed at a tool it does not have —
so it does the only thing left, which is block a whole turn on a sleep.

That happened: an agent babysitting a 20GB download over SSH ran
`powershell -Command "Start-Sleep -Seconds 90; ssh ..."` over and over, tripped the liveness
nudge, and showed no progress for 90 seconds at a stretch. Nothing was broken. The toolbox was
missing half a pair, and the model reasoned correctly about what it had.
"""

import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_authoring.domain.tool_grant_rules import COMPANION_TOOLS, ToolGrantRules

RULES = ToolGrantRules()
ROOT = Path(__file__).resolve().parents[2]


def codes(raw: dict) -> set[str]:
    return {f.code for f in RULES.check(None, raw, [])}


# ── the defect ──────────────────────────────────────────────────────────────
def test_exec_without_process_is_reported():
    assert "COMPANION_TOOL_MISSING" in codes({"tools": {"allow": ["read", "exec"]}})


def test_the_finding_names_both_tools_and_the_fix():
    f = RULES.check(None, {"tools": {"allow": ["exec"]}}, [])[0]
    assert "`exec`" in f.message and "`process`" in f.message
    assert "process" in f.fix and "reload_agent" in f.fix, "an unactionable finding is noise"
    assert f.path == "agent.toml", "point at the file to edit"


def test_denying_the_companion_reads_differently_from_forgetting_it():
    """Deny is explicit intent. Telling someone to add back what they deliberately removed is
    how a check earns a reputation for being wrong."""
    raw = {"tools": {"allow": ["exec"], "deny": ["process"]}}
    assert codes(raw) == {"COMPANION_TOOL_DENIED"}
    f = RULES.check(None, raw, [])[0]
    assert "AGENTS.md" in f.fix, "if it is deliberate, the agent has to be TOLD it has no poll"


# ── silence on everything that is fine ──────────────────────────────────────
def test_both_granted_is_silent():
    assert not codes({"tools": {"allow": ["read", "exec", "process"]}})


def test_neither_granted_is_silent():
    assert not codes({"tools": {"allow": ["read", "write"]}})


def test_no_allow_list_is_silent():
    """No list means "whatever this agent's tier offers" — nothing is being withheld, so there
    is no half-pair to report. Only an EXPLICIT list can be wrong."""
    assert not codes({"tools": {"deny": ["browser"]}})
    assert not codes({"tools": {}})
    assert not codes({})


def test_malformed_shapes_are_silent_rather_than_crashing():
    """Hand-edited TOML. A rule that raises takes the whole report down with it, so a shape it
    cannot read is nothing to say — not an exception."""
    for raw in ({"tools": "exec"}, {"tools": {"allow": "exec"}}, {"tools": {"allow": [None, 1]}}):
        assert not codes(raw)


def test_whitespace_in_a_hand_written_list_still_counts():
    assert not codes({"tools": {"allow": ["exec ", " process"]}})


# ── the map cannot rot ──────────────────────────────────────────────────────
def test_every_tool_named_in_the_map_actually_exists():
    """A curated map is only defensible while it names real tools. If `process` is ever renamed
    or dropped, this fails here rather than emitting advice nobody can follow."""
    src = (ROOT / "plugins" / "shell" / "exec_tool.py").read_text(encoding="utf-8")
    for tool, (companion, _why) in COMPANION_TOOLS.items():
        assert f'name = "{tool}"' in src, f"{tool} no longer exists"
        assert f'name = "{companion}"' in src, f"{companion} no longer exists"


def test_the_pair_is_justified_by_execs_own_description():
    """The reason this pair is in the map at all: exec TELLS the model to use process. If that
    stops being true the pairing needs re-arguing, not silently keeping."""
    src = (ROOT / "plugins" / "shell" / "exec_tool.py").read_text(encoding="utf-8")
    assert "background=true" in src
    assert "process tool to poll it" in src


# ── the agents that ship ────────────────────────────────────────────────────
def test_shipped_agents_are_not_missing_a_companion():
    """agent-builder gained `exec` so it could RUN what it wrote, and was given no way to poll
    a background job — the same mistake it then made in an agent it built. Only the agents that
    actually ship are checked; a dev checkout's scratch agents are the user's business."""
    bad = []
    for agent in ("agent-builder", "main"):
        toml = ROOT / "agents" / agent / "agent.toml"
        if not toml.is_file():
            continue
        raw = tomllib.loads(toml.read_text(encoding="utf-8"))
        bad += [f"{agent}: {f.message}" for f in RULES.check(None, raw, [])]
    assert not bad, "\n".join(bad)

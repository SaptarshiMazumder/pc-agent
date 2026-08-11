"""`sandbox_untrusted_agents` — the development switch, and the proof it cannot weaken anything.

Trust is derived from the marketplace ledger, so the only way to see what a BUYER gets was to
pack and install your own agent before every run. That is enough friction that the sandbox stops
being exercised while it is being built, which is the worst possible time not to exercise it.

This switch makes the same code path run against a local agent. The reason it is safe to have at
all is the single asymmetry these tests exist to pin: **the only transition it can cause is
FIRST_PARTY -> THIRD_PARTY_BUNDLE.** There is no input, in config or in a package, that turns an
untrusted tool into a trusted one through this knob.

The second half of the file matters as much as the first: adding it reordered `classify_origin`,
so every pre-existing decision is re-asserted with the switch UNSET. A development convenience
that quietly changed production classification would be exactly the trade nobody agreed to.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agent_runtime.domain.sandbox import PluginOrigin
from agent_runtime.infrastructure.tools.sandbox.classify import classify_origin


class _Cfg:
    def __init__(self, **kw):
        self.sandbox_untrusted_agents = kw.pop("force", ())
        self.sandbox_trusted_agents = kw.pop("trust_agents", ())
        self.sandbox_trusted_plugins = kw.pop("trust_plugins", ())
        self.sandbox_untrusted_plugins = True


class _Tool:
    def __init__(self, agent_id="", plugin_id="game-kit"):
        self._agent_id = agent_id
        self._plugin_id = plugin_id


LEDGER = frozenset({"downloaded-agent"})


# --- what the switch does ---------------------------------------------------


def test_it_forces_a_local_agent_to_be_treated_as_installed():
    """The whole feature: no packing, no installing, same classification a buyer produces."""
    origin = classify_origin(_Tool("game-master"), _Cfg(force=("game-master",)), LEDGER)
    assert origin is PluginOrigin.THIRD_PARTY_BUNDLE
    assert origin.is_untrusted


def test_it_names_agents_not_plugins():
    """Scoped to the agent, because that is the unit a user installs and the unit trust is
    derived from. A plugin id here silently does nothing rather than half-working."""
    assert classify_origin(_Tool("game-master"), _Cfg(force=("game-kit",)), LEDGER) is (
        PluginOrigin.FIRST_PARTY
    )


def test_it_outranks_every_exemption():
    """Otherwise the switch whose entire purpose is 'show me what a buyer gets' could be silently
    cancelled by a trust knob left over from something else."""
    cfg = _Cfg(
        force=("game-master",),
        trust_agents=("game-master",),
        trust_plugins=("game-kit",),
    )
    assert classify_origin(_Tool("game-master"), cfg, LEDGER).is_untrusted


# --- what it CANNOT do — the security property ------------------------------


@pytest.mark.parametrize(
    ("agent", "ledger", "why"),
    [
        ("downloaded-agent", LEDGER, "an agent in the ledger"),
        ("anything", None, "an unreadable ledger (fail-closed)"),
    ],
)
def test_it_can_never_make_an_untrusted_tool_trusted(agent, ledger, why):
    """THE property. The knob has one direction. Naming an already-untrusted agent leaves it
    untrusted; there is no spelling of this config that grants trust."""
    assert classify_origin(_Tool(agent), _Cfg(force=(agent,)), ledger).is_untrusted, why


def test_a_package_cannot_reach_it():
    """Operator config only. classify_origin reads `config`, never the tool's manifest or its
    agent.toml — so nothing that travels inside a .agentpkg can set or clear this."""
    tool = _Tool("downloaded-agent")
    tool.sandbox_untrusted_agents = ()  # a hostile package trying to speak for itself
    tool.trusted = True
    assert classify_origin(tool, _Cfg(), LEDGER).is_untrusted


def test_a_shared_plugin_is_unaffected():
    """Shared plugins are not a sandboxed tier at all — the switch must not invent one, or
    naming an agent would start boxing the operator's own catalog."""
    assert classify_origin(_Tool(agent_id=""), _Cfg(force=("",)), LEDGER) is (
        PluginOrigin.FIRST_PARTY
    )


# --- the default path is byte-for-byte what it was --------------------------
# Adding the switch reordered classify_origin. With it unset, every prior decision must stand.

OFF = _Cfg()


def test_default_a_locally_authored_agent_stays_trusted():
    assert classify_origin(_Tool("game-master"), OFF, LEDGER) is PluginOrigin.FIRST_PARTY


def test_default_an_installed_agent_is_untrusted():
    assert classify_origin(_Tool("downloaded-agent"), OFF, LEDGER).is_untrusted


def test_default_an_unreadable_ledger_still_fails_closed():
    assert classify_origin(_Tool("anything"), OFF, None).is_untrusted


def test_default_a_shared_plugin_is_trusted():
    assert classify_origin(_Tool(agent_id=""), OFF, LEDGER) is PluginOrigin.FIRST_PARTY


def test_default_trusted_agents_still_exempts_an_installed_agent():
    cfg = _Cfg(trust_agents=("downloaded-agent",))
    assert classify_origin(_Tool("downloaded-agent"), cfg, LEDGER) is PluginOrigin.FIRST_PARTY


def test_default_trusted_plugins_still_exempts_an_installed_agents_plugin():
    cfg = _Cfg(trust_plugins=("game-kit",))
    assert classify_origin(_Tool("downloaded-agent", "game-kit"), cfg, LEDGER) is (
        PluginOrigin.FIRST_PARTY
    )


def test_no_config_at_all_behaves_as_before():
    assert classify_origin(_Tool("downloaded-agent"), None, LEDGER).is_untrusted
    assert classify_origin(_Tool("local"), None, LEDGER) is PluginOrigin.FIRST_PARTY


# ── `*` — the version that survives building a second agent ─────────────────
# Naming ids one at a time is the friction that stopped the sandbox being exercised at all:
# you build an agent, forget to add it, and test the trusted path by accident.
def test_star_forces_every_agent():
    cfg = _Cfg(force=("*",))
    for agent in ("comfyui-workflow-architect", "built-tomorrow", "agent-builder"):
        assert classify_origin(_Tool(agent), cfg, LEDGER).is_untrusted


def test_star_outranks_every_exemption():
    """The same asymmetry the id-list has: a switch whose whole purpose is 'show me what a buyer
    gets' must not be silently cancelled by an exemption left in config."""
    cfg = _Cfg(force=("*",), trust_agents=("game-master",), trust_plugins=("game-kit",))
    assert classify_origin(_Tool("game-master"), cfg, LEDGER).is_untrusted


def test_star_never_touches_a_shared_plugin():
    """`*` means every AGENT, not every tool. A shared plugin has no _agent_id and is the
    operator's own code — sandboxing it would box the daemon's own hands."""
    assert classify_origin(_Tool(""), _Cfg(force=("*",)), LEDGER) is PluginOrigin.FIRST_PARTY


def test_star_stays_a_literal_id_on_the_trusting_knobs():
    """On a knob that RELAXES, a wildcard would switch the sandbox off wholesale from one config
    line — the only thing none of these may do."""
    assert classify_origin(_Tool("downloaded-agent"), _Cfg(trust_agents=("*",)), LEDGER) is (
        PluginOrigin.THIRD_PARTY_BUNDLE
    )

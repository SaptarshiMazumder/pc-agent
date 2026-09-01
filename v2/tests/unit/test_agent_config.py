"""Per-agent config layered over the daemon's.

The rules that will be argued about later, pinned now while they are cheap:

  * the default is SAFE — an agent with no entry behaves exactly as it does today
  * the flag is KEY BY KEY, not all-or-nothing
  * turning the flag off does not delete anything
  * provider keys are not overridable, at all

The resolver is pure, so all of that is testable without a daemon, a file, or a UI.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_runtime.domain.agent_config import (
    AGENT,
    AUTHOR,
    DAEMON,
    OVERRIDABLE_KEYS,
    agent_entry,
    authored_values,
    overrides_daemon,
    resolve,
    user_may_edit,
)


class FakeConfig:
    """Only the attributes the resolver reads."""

    def __init__(self, agents=None, **daemon):
        self.model = daemon.get("model", "openai/gpt-5")
        self.reasoning_effort = daemon.get("reasoning_effort", "medium")
        self.max_turns = daemon.get("max_turns", 100)
        self.model_fallbacks = daemon.get("model_fallbacks", ["gemini/gemini-2.5-pro"])
        self.cost_efficiency = daemon.get("cost_efficiency", {})
        self.verify_tool = daemon.get("verify_tool", False)
        self.memory_enabled = daemon.get("memory_enabled", True)
        if agents is not None:
            self.agents = agents


# ── the default is doing nothing ────────────────────────────────────────────
def test_an_agent_with_no_entry_gets_the_daemons_values():
    """Every agent that exists today is this case. If it resolved to anything else, shipping
    this feature would silently change how every one of them runs."""
    cfg = FakeConfig(agents={})
    values, sources = resolve(cfg, "anything")
    assert values["model"] == "openai/gpt-5"
    assert set(sources.values()) == {DAEMON}


def test_a_config_with_no_agents_key_at_all_is_fine():
    """An older config file predates the key. Absent is not an error, it is 'nobody has set
    anything' — which is a real state, not a failure being papered over."""
    cfg = FakeConfig()  # no .agents attribute whatsoever
    values, _ = resolve(cfg, "anything")
    assert values["model"] == "openai/gpt-5"


def test_the_flag_defaults_to_on():
    cfg = FakeConfig(agents={"x": {"model": "anthropic/claude-opus-4-8"}})
    assert overrides_daemon(cfg, "x") is True
    assert resolve(cfg, "x")[0]["model"] == "anthropic/claude-opus-4-8"


# ── key by key ──────────────────────────────────────────────────────────────
def test_setting_one_key_does_not_orphan_the_others():
    """All-or-nothing would leave this agent with no reasoning effort, no turn limit and no
    fallbacks the moment it named a model."""
    cfg = FakeConfig(agents={"x": {"model": "anthropic/claude-opus-4-8"}})
    values, sources = resolve(cfg, "x")
    assert values["model"] == "anthropic/claude-opus-4-8"
    assert sources["model"] == AGENT
    assert values["max_turns"] == 100
    assert sources["max_turns"] == DAEMON
    assert values["model_fallbacks"] == ["gemini/gemini-2.5-pro"]


def test_sources_says_which_layer_each_value_came_from():
    """A page that shows a value without saying where it came from is the page that showed
    GPT-5 while gemini answered every turn."""
    cfg = FakeConfig(agents={"x": {"model": "deepseek/deepseek-v4-pro", "max_turns": 5}})
    _, sources = resolve(cfg, "x")
    assert sources["model"] == AGENT
    assert sources["max_turns"] == AGENT
    assert sources["reasoning_effort"] == DAEMON


def test_cost_efficiency_is_overridable_whole():
    """It is a dict, and the agent replaces it entirely — a half-merged router config (enabled
    from one layer, models from another) is not something anyone could reason about."""
    cfg = FakeConfig(
        cost_efficiency={"enabled": True, "text_model": "gemini/gemini-3.1-pro-preview"},
        agents={"x": {"cost_efficiency": {"enabled": False}}},
    )
    values, sources = resolve(cfg, "x")
    assert values["cost_efficiency"] == {"enabled": False}
    assert sources["cost_efficiency"] == AGENT


# ── the flag off ────────────────────────────────────────────────────────────
def test_an_agents_values_win_with_no_switch_to_turn_them_off():
    """THE RULE, AND THERE IS ONLY ONE. An agent's own settings decide how that agent runs.

    There used to be an `override_default` switch, and "off" meant "use the daemon's values". It
    was a trap next to cost efficiency — a knob that OVERWRITES the model on every turn — because
    an agent could name its model, have the daemon's cheap one answer instead, and show nothing on
    screen that explained which layer had won. One layer decides; nothing to arbitrate."""
    cfg = FakeConfig(agents={"x": {"model": "deepseek/deepseek-v4-pro"}})
    values, sources = resolve(cfg, "x")

    assert values["model"] == "deepseek/deepseek-v4-pro"
    assert sources["model"] == AGENT


def test_a_stored_override_flag_from_before_is_ignored_rather_than_obeyed():
    """Configs written by the old build still carry the key. It is not in OVERRIDABLE_KEYS, so it
    is not a knob — and crucially `false` no longer suppresses the agent's own values, or every
    config saved before this change would silently keep the old behaviour."""
    cfg = FakeConfig(
        agents={"x": {"override_default": False, "model": "deepseek/deepseek-v4-pro"}}
    )
    values, sources = resolve(cfg, "x")

    assert values["model"] == "deepseek/deepseek-v4-pro"
    assert sources["model"] == AGENT
    assert "override_default" not in values, "the flag itself is not a setting"


# ── the whitelist ───────────────────────────────────────────────────────────
def test_provider_keys_are_not_overridable():
    """One shared .env. Per-agent copies of the same secret would have no sane precedence, and
    the rule that strips envValues for an installed agent assumes a single source."""
    for key in ("api_keys", "openai_api_key", "env", "keys"):
        assert key not in OVERRIDABLE_KEYS


def test_machine_wide_knobs_are_not_overridable():
    """An agent offering to change the daemon's port is offering to break the install from
    inside a package the user trusted for one job."""
    for key in ("port", "host", "state_dir", "workspace", "agents_dir", "subagent_max"):
        assert key not in OVERRIDABLE_KEYS


def test_a_key_outside_the_whitelist_never_reaches_the_run():
    cfg = FakeConfig(agents={"x": {"port": 9999, "model": "deepseek/deepseek-v4-pro"}})
    values, _ = resolve(cfg, "x")
    assert "port" not in values
    assert values["model"] == "deepseek/deepseek-v4-pro"


def test_the_flag_itself_is_not_a_knob():
    values, _ = resolve(FakeConfig(agents={"x": {"override_default": True}}), "x")
    assert "override_default" not in values


# ── malformed shapes ────────────────────────────────────────────────────────
def test_a_non_dict_agents_key_is_treated_as_absent():
    """Hand-edited config. There is nothing to resolve from a list, and no value to invent, so
    this falls to the daemon's — the same genuine path as 'no entry'."""
    cfg = FakeConfig(agents=["not", "a", "dict"])
    assert resolve(cfg, "x")[0]["model"] == "openai/gpt-5"


def test_a_non_dict_entry_is_treated_as_absent():
    cfg = FakeConfig(agents={"x": "openai/gpt-5"})
    assert resolve(cfg, "x")[0]["model"] == "openai/gpt-5"


def test_every_overridable_key_is_present_in_the_result():
    """The caller reads values[key] directly; a missing key would be a KeyError at run time."""
    values, sources = resolve(FakeConfig(agents={}), "x")
    assert set(values) == set(OVERRIDABLE_KEYS)
    assert set(sources) == set(OVERRIDABLE_KEYS)


# ── the bug this replaces ───────────────────────────────────────────────────
# One router was built for the whole daemon at boot and it OVERWROTE the model on every turn,
# so an agent that named its own model had that choice silently discarded whenever
# cost-efficiency was on anywhere. These pin the composition that makes it impossible.
def test_an_agent_can_turn_cost_efficiency_off_while_the_daemon_has_it_on():
    from agent_runtime.infrastructure.llm.model_router import router_for

    cfg = FakeConfig(
        model="openai/gpt-5",
        cost_efficiency={"enabled": True, "text_model": "gemini/gemini-3.1-pro-preview"},
        agents={"x": {"cost_efficiency": {"enabled": False}}},
    )
    values, _ = resolve(cfg, "x")
    assert router_for(values["cost_efficiency"]) is None, (
        "no router means the resolved model is what actually runs — the whole point"
    )
    assert values["model"] == "openai/gpt-5"


def test_an_agent_can_run_its_own_cheap_brain_while_the_daemon_runs_none():
    from agent_runtime.infrastructure.llm.model_router import router_for

    cfg = FakeConfig(
        cost_efficiency={},  # daemon: off
        agents={
            "x": {
                "cost_efficiency": {
                    "enabled": True,
                    "text_model": "deepseek/deepseek-v4-pro",
                    "vision_model": "gemini/gemini-3.1-pro-preview",
                }
            }
        },
    )
    values, _ = resolve(cfg, "x")
    router = router_for(values["cost_efficiency"])
    assert router is not None
    assert router(default_model="ignored", messages=[]) == "deepseek/deepseek-v4-pro"


def test_an_agent_with_no_entry_still_gets_the_daemons_router():
    """Turning this feature on must not quietly disable cost-efficiency for everyone else."""
    from agent_runtime.infrastructure.llm.model_router import router_for

    cfg = FakeConfig(
        cost_efficiency={"enabled": True, "text_model": "deepseek/deepseek-v4-pro"},
        agents={},
    )
    values, _ = resolve(cfg, "untouched-agent")
    assert router_for(values["cost_efficiency"]) is not None


# ── the author's layer ──────────────────────────────────────────────────────
# THE MISSING LAYER. Before this an agent could describe what it was but not how it had to run,
# so it arrived on a stranger's machine configured by a stranger. An author who needs a vision
# model for their agent to work at all says so once, in `agent.config.json`, and it ships.


def test_an_authored_value_beats_the_daemon():
    cfg = FakeConfig(agents={})
    values, sources = resolve(cfg, "vision-bot", authored={"model": "gemini/gemini-3.1-pro"})
    assert values["model"] == "gemini/gemini-3.1-pro"
    assert sources["model"] == AUTHOR
    # ...and only that key moved. Key by key, exactly like the installer's layer.
    assert values["max_turns"] == 100
    assert sources["max_turns"] == DAEMON


def test_no_authored_config_resolves_exactly_as_before():
    """Every agent built before this feature ships none. If they resolved differently, adding the
    layer would silently change how all of them run."""
    cfg = FakeConfig(agents={"a": {"model": "x"}})
    assert resolve(cfg, "a") == resolve(cfg, "a", authored=None)
    assert resolve(cfg, "a", authored={})[1]["model"] == AGENT


def test_the_author_cannot_move_the_daemons_port():
    """The whitelist, from the other side. `agent.config.json` arrives from whoever wrote the
    agent — so an author who puts `port` in it must not be able to reconfigure the machine of
    everyone who installs it."""
    assert authored_values({"port": 9999, "model": "m", "user_editable": True}) == {"model": "m"}


# ── locked by default ───────────────────────────────────────────────────────


def test_the_installer_cannot_override_a_locked_authored_value():
    """The default. An agent behaves the same everywhere unless its author opted out."""
    cfg = FakeConfig(agents={"vision-bot": {"model": "openai/gpt-4"}})
    values, sources = resolve(cfg, "vision-bot", authored={"model": "gemini/gemini-3.1-pro"})
    assert values["model"] == "gemini/gemini-3.1-pro"
    assert sources["model"] == AUTHOR


def test_user_editable_hands_the_decision_back():
    cfg = FakeConfig(agents={"vision-bot": {"model": "openai/gpt-4"}})
    values, sources = resolve(
        cfg, "vision-bot", authored={"user_editable": True, "model": "gemini/gemini-3.1-pro"}
    )
    assert values["model"] == "openai/gpt-4"
    assert sources["model"] == AGENT


def test_locking_covers_only_what_the_author_actually_set():
    """THE DISTINCTION THAT MATTERS. "My agent needs Gemini for vision" must not also mean "and
    you may not change the turn limit." A knob the author never touched is not locked, because
    there is nothing there to protect."""
    cfg = FakeConfig(agents={"vision-bot": {"model": "openai/gpt-4", "max_turns": 12}})
    values, sources = resolve(cfg, "vision-bot", authored={"model": "gemini/gemini-3.1-pro"})

    assert values["model"] == "gemini/gemini-3.1-pro" and sources["model"] == AUTHOR
    assert values["max_turns"] == 12 and sources["max_turns"] == AGENT


def test_user_editable_defaults_to_false():
    """Stated as its own test because it is a POLICY, not an implementation detail: the author's
    choices are the author's unless they say otherwise."""
    assert user_may_edit(None) is False
    assert user_may_edit({}) is False
    assert user_may_edit({"model": "m"}) is False
    assert user_may_edit({"user_editable": True}) is True

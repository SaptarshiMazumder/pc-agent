"""Per-account config — X and Y on one daemon, and neither can reach the other or the machine.

The bug this replaces was not a storage bug. ``config.set`` wrote the daemon's own config file and
then ``setattr``-ed the values onto the live ``Config`` object, so on a hosted daemon ANY signed-in
tenant could change the brain model, the fallbacks, the tool models and the enabled-tool set for
everybody else, on their next message, with no restart. What is pinned here is the whole property
in three parts:

  ISOLATION      X's overlay is X's. Y's save is invisible to X, and neither can write the master.
  INHERITANCE    an untouched key follows the master forever ("seeded from the latest state"),
                 which a copy-at-signup would break the first time a default changed.
  DESKTOP        with no account the effective config is the SAME OBJECT, not a copy — the
                 single-user path is untouched rather than merely equivalent.

The read-side filter gets its own tests because it is the half that does not trust the filesystem:
validating writes only protects against the API, and the API is not the only thing that can put a
file on a volume.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agent_runtime.infrastructure import account_config, accounts, user_state

X = "acct_xxx"
Y = "acct_yyy"


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """No cached overlay and no ambient account may leak between tests — both are process-wide.

    ``accounts.enabled()`` is forced ON because that is what a SHARED daemon looks like, and a
    shared daemon is the only place per-account config applies. A desktop that has signed into the
    platform carries an account too, and must keep using the machine's config — see
    `test_a_signed_in_desktop_still_uses_the_machines_config`."""
    account_config.clear_cache()
    monkeypatch.setattr(accounts, "enabled", lambda: True)
    token = accounts.current_account.set(None)
    yield
    accounts.current_account.reset(token)
    account_config.clear_cache()


def _master(tmp_path, **over):
    """A stand-in for the daemon's own Config: the machine's values, shared by every tenant."""
    values = dict(
        state_dir=tmp_path / "state",
        config_path=str(tmp_path / "config.json"),
        model="openai/gpt-5",
        model_fallbacks=[],
        reasoning_effort="off",
        cost_efficiency={"enabled": False},
        plugins={"figure-art": {"tools": {"generate_artwork": {"model": "master/nano"}}}},
        agents={},
        max_turns=40,
        llm_idle_timeout_seconds=60,
        llm_request_timeout_seconds=600,
        model_defaults={},
        # machine-only, present so a test can prove they never move
        port=8765,
        host="127.0.0.1",
        state_dir_is_machine=True,
    )
    values.update(over)
    return SimpleNamespace(**values)


def _signed_in_as(account_id: str):
    return accounts.current_account.set({"account_id": account_id})


# ── isolation ────────────────────────────────────────────────────────────────────────────────
def test_x_and_y_each_see_their_own_model_and_neither_sees_the_other(tmp_path):
    cfg = _master(tmp_path)
    account_config.write_overlay(cfg, X, {"model": "anthropic/claude-opus-5"})
    account_config.write_overlay(cfg, Y, {"model": "gemini/gemini-3.1-pro-preview"})

    assert account_config.for_account(cfg, X).model == "anthropic/claude-opus-5"
    assert account_config.for_account(cfg, Y).model == "gemini/gemini-3.1-pro-preview"
    # and the machine itself never moved
    assert cfg.model == "openai/gpt-5"


def test_ys_save_does_not_reach_x_even_after_x_has_already_been_resolved(tmp_path):
    """The cache is the risk here: X resolved first, so a naive implementation could hand X's
    entry to Y — or worse, hand Y's later write to X."""
    cfg = _master(tmp_path)
    account_config.write_overlay(cfg, X, {"model": "x/model"})
    assert account_config.for_account(cfg, X).model == "x/model"

    account_config.write_overlay(cfg, Y, {"model": "y/model", "reasoning_effort": "high"})

    assert account_config.for_account(cfg, X).model == "x/model"
    assert account_config.for_account(cfg, X).reasoning_effort == "off"
    assert account_config.for_account(cfg, Y).model == "y/model"


def test_each_overlay_is_a_file_inside_that_accounts_own_subtree(tmp_path):
    """Isolation is enforced by the tenant fence around accounts/<id>/, so the file has to be
    INSIDE it — not beside it, and not in a shared directory keyed by name."""
    cfg = _master(tmp_path)
    px = account_config.overlay_path(cfg, X)
    py = account_config.overlay_path(cfg, Y)
    assert px == user_state.account_root(cfg.state_dir, X) / "config.json"
    assert px != py
    assert user_state.account_root(cfg.state_dir, X) in px.parents


def test_a_hostile_account_id_cannot_escape_the_account_root(tmp_path):
    cfg = _master(tmp_path)
    path = account_config.overlay_path(cfg, "../../etc/passwd")
    assert ".." not in path.parts
    assert (cfg.state_dir / "accounts") in path.parents


# ── the master is not theirs ─────────────────────────────────────────────────────────────────
def test_writing_an_overlay_never_touches_the_master_file(tmp_path):
    cfg = _master(tmp_path)
    master = Path(cfg.config_path)
    master.write_text(json.dumps({"model": "openai/gpt-5"}), encoding="utf-8")
    before = master.read_text(encoding="utf-8")

    account_config.write_overlay(cfg, X, {"model": "x/model", "max_turns": 2})

    assert master.read_text(encoding="utf-8") == before


def test_machine_only_keys_are_named_so_a_refusal_can_explain_itself(tmp_path):
    assert account_config.machine_only({"model": 1, "port": 2, "state_dir": 3}) == [
        "port",
        "state_dir",
    ]
    assert account_config.machine_only({"model": 1, "plugins": {}, "agents": {}}) == []


def test_a_machine_key_is_ignored_even_when_it_is_already_in_the_file(tmp_path):
    """The read-side filter. Someone with volume access — a restored backup, an older build, a
    hand edit — cannot widen what an overlay may do, because the merge itself refuses."""
    cfg = _master(tmp_path)
    path = account_config.overlay_path(cfg, X)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"model": "x/model", "port": 9999}), encoding="utf-8")

    eff = account_config.for_account(cfg, X)
    assert eff.model == "x/model"
    assert eff.port == 8765  # the machine's, untouched


def test_write_overlay_persists_only_per_user_keys(tmp_path):
    cfg = _master(tmp_path)
    account_config.write_overlay(cfg, X, {"model": "x/model", "port": 1, "state_dir": "/tmp"})
    stored = json.loads(account_config.overlay_path(cfg, X).read_text(encoding="utf-8"))
    assert stored == {"model": "x/model"}


# ── inheritance from the master ──────────────────────────────────────────────────────────────
def test_an_untouched_key_follows_the_master_rather_than_a_signup_snapshot(tmp_path):
    """The whole argument for storing CHANGES instead of a copy: raise a default and every user
    who never overrode it moves with you."""
    cfg = _master(tmp_path)
    account_config.write_overlay(cfg, X, {"reasoning_effort": "high"})
    assert account_config.for_account(cfg, X).model == "openai/gpt-5"

    account_config.clear_cache()
    newer = _master(tmp_path, model="openai/gpt-6")
    eff = account_config.for_account(newer, X)
    assert eff.model == "openai/gpt-6"  # inherited the NEW master
    assert eff.reasoning_effort == "high"  # kept their own choice


def test_setting_one_tool_model_keeps_every_other_plugin_entry(tmp_path):
    """A settings page sends one key. Replacing `plugins` wholesale would silently drop every
    tool the master configures — which reads to the user as "my agent broke"."""
    cfg = _master(tmp_path)
    account_config.write_overlay(
        cfg, X, {"plugins": {"vision": {"tools": {"verify_figure": {"model": "x/judge"}}}}}
    )
    plugins = account_config.for_account(cfg, X).plugins
    assert plugins["vision"]["tools"]["verify_figure"]["model"] == "x/judge"
    assert plugins["figure-art"]["tools"]["generate_artwork"]["model"] == "master/nano"


def test_two_users_configure_the_same_tool_differently(tmp_path):
    cfg = _master(tmp_path)
    for acct, model in ((X, "x/nano"), (Y, "y/nano")):
        account_config.write_overlay(
            cfg, acct, {"plugins": {"figure-art": {"tools": {"generate_artwork": {"model": model}}}}}
        )
    ex = account_config.for_account(cfg, X).plugins["figure-art"]["tools"]["generate_artwork"]
    ey = account_config.for_account(cfg, Y).plugins["figure-art"]["tools"]["generate_artwork"]
    assert (ex["model"], ey["model"]) == ("x/nano", "y/nano")


# ── per-agent, per-user ──────────────────────────────────────────────────────────────────────
def test_each_user_overrides_the_same_agent_independently(tmp_path):
    """"Every agent of every user" — the agents block is per-user, so X's figure-creator and Y's
    figure-creator are configured separately even though it is one installed agent."""
    from agent_runtime.domain.agent_config import resolve

    cfg = _master(tmp_path)
    account_config.write_overlay(cfg, X, {"agents": {"figure-creator": {"model": "x/for-figures"}}})
    account_config.write_overlay(cfg, Y, {"agents": {"figure-creator": {"model": "y/for-figures"}}})

    vx, _ = resolve(account_config.for_account(cfg, X), "figure-creator")
    vy, _ = resolve(account_config.for_account(cfg, Y), "figure-creator")
    assert vx["model"] == "x/for-figures"
    assert vy["model"] == "y/for-figures"
    # an agent they did NOT configure still resolves to their own daemon-level value
    account_config.write_overlay(cfg, X, {"model": "x/default"})
    vx_other, _ = resolve(account_config.for_account(cfg, X), "weather")
    assert vx_other["model"] == "x/default"


def test_one_agents_override_does_not_disturb_another_agents(tmp_path):
    cfg = _master(tmp_path)
    account_config.write_overlay(cfg, X, {"agents": {"a": {"model": "m-a"}}})
    account_config.write_overlay(cfg, X, {"agents": {"b": {"model": "m-b"}}})
    agents = account_config.for_account(cfg, X).agents
    assert agents["a"]["model"] == "m-a" and agents["b"]["model"] == "m-b"


# ── desktop / signed-out ─────────────────────────────────────────────────────────────────────
def test_with_no_account_the_effective_config_is_the_same_object(tmp_path):
    """Identity, not equality. The single-user path must not acquire a copy on the hot path, and
    'same object' is the only assertion that proves nothing was rebound behind its back."""
    cfg = _master(tmp_path)
    assert account_config.effective(cfg) is cfg
    assert account_config.for_account(cfg, None) is cfg


def test_a_signed_in_connection_resolves_its_own_config_through_the_contextvar(tmp_path):
    cfg = _master(tmp_path)
    account_config.write_overlay(cfg, X, {"model": "x/model"})
    token = _signed_in_as(X)
    try:
        assert account_config.effective(cfg).model == "x/model"
    finally:
        accounts.current_account.reset(token)
    assert account_config.effective(cfg) is cfg  # back to the machine's


# ── failure modes ────────────────────────────────────────────────────────────────────────────
def test_a_corrupt_overlay_degrades_to_the_master_and_never_raises(tmp_path, caplog):
    cfg = _master(tmp_path)
    path = account_config.overlay_path(cfg, X)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json at all", encoding="utf-8")

    eff = account_config.for_account(cfg, X)
    assert eff.model == "openai/gpt-5"


def test_an_overlay_that_is_not_an_object_is_ignored(tmp_path):
    cfg = _master(tmp_path)
    path = account_config.overlay_path(cfg, X)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert account_config.for_account(cfg, X).model == "openai/gpt-5"


def test_an_account_with_no_overlay_gets_the_master_values(tmp_path):
    cfg = _master(tmp_path)
    eff = account_config.for_account(cfg, X)
    assert eff.model == "openai/gpt-5"
    assert eff.plugins["figure-art"]["tools"]["generate_artwork"]["model"] == "master/nano"


def test_replace_overlay_drops_machine_keys_and_keeps_the_rest(tmp_path):
    cfg = _master(tmp_path)
    account_config.replace_overlay(cfg, X, {"model": "x/m", "port": 22, "reasoning_effort": "high"})
    stored = json.loads(account_config.overlay_path(cfg, X).read_text(encoding="utf-8"))
    assert stored == {"model": "x/m", "reasoning_effort": "high"}


def test_replace_overlay_really_replaces(tmp_path):
    """Unlike write_overlay, which merges — the Advanced editor's document IS the whole file."""
    cfg = _master(tmp_path)
    account_config.write_overlay(cfg, X, {"model": "x/m", "max_turns": 3})
    account_config.replace_overlay(cfg, X, {"model": "x/other"})
    stored = json.loads(account_config.overlay_path(cfg, X).read_text(encoding="utf-8"))
    assert stored == {"model": "x/other"}


# ── the seam: every tool and the brain resolve against the CALLER's config ────────────────────
@pytest.fixture
def resolver_installed():
    """Install the account-aware resolver the container installs at boot, then remove it. The
    hook is process-global, so a test that leaves it behind changes every later test."""
    from agent_runtime.application import tool_models

    tool_models.set_effective_config_resolver(account_config.effective)
    yield tool_models
    tool_models.set_effective_config_resolver(None)


def test_each_account_resolves_its_own_tool_model(tmp_path, resolver_installed):
    """THE integration point. Tools capture the daemon's config at registration, so without the
    seam every tenant's figure generation would run on whatever the machine says."""
    tool_models = resolver_installed
    cfg = _master(tmp_path)
    for acct, model in ((X, "x/nano"), (Y, "y/nano")):
        account_config.write_overlay(
            cfg, acct, {"plugins": {"figure-art": {"tools": {"generate_artwork": {"model": model}}}}}
        )

    seen = {}
    for acct in (X, Y):
        token = _signed_in_as(acct)
        try:
            seen[acct] = tool_models.resolve_tool_model(cfg, "figure-art", "generate_artwork")
        finally:
            accounts.current_account.reset(token)

    assert seen == {X: "x/nano", Y: "y/nano"}
    # …and with nobody signed in, the machine's own value
    assert tool_models.resolve_tool_model(cfg, "figure-art", "generate_artwork") == "master/nano"


def test_each_account_resolves_its_own_brain_model(tmp_path, resolver_installed):
    tool_models = resolver_installed
    cfg = _master(tmp_path)
    account_config.write_overlay(cfg, X, {"model": "x/brain"})

    token = _signed_in_as(X)
    try:
        assert tool_models.brain_model(cfg) == "x/brain"
    finally:
        accounts.current_account.reset(token)
    token = _signed_in_as(Y)
    try:
        assert tool_models.brain_model(cfg) == "openai/gpt-5"  # never X's
    finally:
        accounts.current_account.reset(token)


def test_a_tool_knob_is_per_account_too(tmp_path, resolver_installed):
    """Not just models: `tool_config` is how every tool reads every knob it has."""
    tool_models = resolver_installed
    cfg = _master(tmp_path)
    account_config.write_overlay(
        cfg, X, {"plugins": {"figure-art": {"tools": {"generate_artwork": {"resolution": "4K"}}}}}
    )
    token = _signed_in_as(X)
    try:
        got = tool_models.tool_config(cfg, "figure-art", "generate_artwork", "resolution", "1K")
    finally:
        accounts.current_account.reset(token)
    assert got == "4K"
    assert tool_models.tool_config(cfg, "figure-art", "generate_artwork", "resolution", "1K") == "1K"


def test_a_broken_resolver_never_breaks_a_turn(tmp_path):
    """The seam fails OPEN to the machine's config — a turn must not die because an overlay
    could not be read."""
    from agent_runtime.application import tool_models

    def boom(_config):
        raise RuntimeError("disk on fire")

    tool_models.set_effective_config_resolver(boom)
    try:
        assert tool_models.brain_model(_master(tmp_path)) == "openai/gpt-5"
    finally:
        tool_models.set_effective_config_resolver(None)


def test_a_signed_in_desktop_still_resolves_the_machines_config(tmp_path, monkeypatch):
    """The other half of the same rule, at the resolution layer: an account on the connection is
    not enough. Only a daemon that REQUIRES accounts — i.e. a shared one — resolves per account."""
    cfg = _master(tmp_path)
    account_config.write_overlay(cfg, X, {"model": "x/model"})
    monkeypatch.setattr(accounts, "enabled", lambda: False)  # personal daemon
    token = _signed_in_as(X)
    try:
        assert account_config.effective(cfg) is cfg  # same object: nothing per-account happens
    finally:
        accounts.current_account.reset(token)

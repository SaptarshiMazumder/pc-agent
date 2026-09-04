"""``config.get`` / ``config.set`` at the account boundary — the API edge of per-account config.

The unit next door proves the overlay MERGES correctly. This one proves the gateway never lets a
tenant out of it, because that is where the original hole was: `config.set` accepted a patch from
any signed-in connection, wrote the daemon's own file, and `setattr`-ed the values onto the live
Config — one tenant's Save reaching every other tenant's next turn.

Three refusals carry the security story, and each is asserted BY NAME rather than by "it didn't
change anything": a save that silently no-ops is the failure mode that wastes an afternoon.

  keys           the machine's .env is one process environment shared by every tenant
  machine keys   ports, paths, storage roots, the sandbox — the deployment's, not the user's
  the master     never opened; `raw` is bounded to the account's own overlay

Plus the property that makes the change safe to ship: with no account on the connection, the
desktop path is byte-for-byte what it was — master file, hot-apply and all.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agent_runtime.infrastructure import account_config, accounts
from agent_runtime.presentation import gateway as gateway_module
from agent_runtime.presentation.gateway import Gateway

X = "acct_xxx"
Y = "acct_yyy"


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """Hosted by default: `enabled()` ON is what makes a daemon multi-tenant, and it is the other
    half of the routing decision (the connection's account is the first)."""
    account_config.clear_cache()
    monkeypatch.setattr(accounts, "enabled", lambda: True)
    token = accounts.current_account.set(None)
    yield
    accounts.current_account.reset(token)
    account_config.clear_cache()


@pytest.fixture
def master_file(tmp_path, monkeypatch):
    """Point the MASTER config at a temp file. Without this a test that exercises the desktop
    path would rewrite the developer's own ~/.agentd/config.json."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"model": "openai/gpt-5"}, indent=2), encoding="utf-8")
    monkeypatch.setattr(gateway_module, "_config_file_path", lambda: path)
    return path


def _gateway(tmp_path) -> Gateway:
    cfg = SimpleNamespace(
        state_dir=tmp_path / "state",
        config_path=str(tmp_path / "config.json"),
        agent_id="main",
        agent_name="jarvis",
        model="openai/gpt-5",
        model_fallbacks=[],
        reasoning_effort="off",
        cost_efficiency={"enabled": False},
        plugins={},
        agents={},
        max_turns=40,
        port=8765,
        accounts={},
        distribution=None,
        model_proxy={},
        model_gateway={},
        mcp_servers=[],
    )
    return Gateway(config=cfg, service=None, registry=None)


def _as(account_id: str):
    return accounts.current_account.set({"account_id": account_id})


# ── writes land in the account's overlay ─────────────────────────────────────────────────────
def test_an_account_save_writes_its_overlay_and_leaves_the_master_alone(tmp_path, master_file):
    gw = _gateway(tmp_path)
    before = master_file.read_text(encoding="utf-8")
    token = _as(X)
    try:
        res = gw._config_set({"patch": {"model": "x/model"}})
    finally:
        accounts.current_account.reset(token)

    assert res["saved"] is True
    assert Path(res["path"]) == account_config.overlay_path(gw.config, X)
    assert master_file.read_text(encoding="utf-8") == before


def test_an_account_save_never_mutates_the_live_shared_config(tmp_path, master_file):
    """THE bug. The hot-apply is what made a tenant's Save change everyone's next turn."""
    gw = _gateway(tmp_path)
    token = _as(X)
    try:
        gw._config_set({"patch": {"model": "x/model", "max_turns": 2}})
    finally:
        accounts.current_account.reset(token)

    assert gw.config.model == "openai/gpt-5"
    assert gw.config.max_turns == 40


def test_an_account_save_takes_effect_without_a_restart_for_that_account_only(tmp_path, master_file):
    gw = _gateway(tmp_path)
    token = _as(X)
    try:
        res = gw._config_set({"patch": {"model": "x/model"}})
        assert res["restartRecommended"] is False
        assert gw._config_get(None)["values"]["model"] == "x/model"
    finally:
        accounts.current_account.reset(token)

    token = _as(Y)
    try:
        assert gw._config_get(None)["values"]["model"] == "openai/gpt-5"
    finally:
        accounts.current_account.reset(token)


def test_two_accounts_edit_the_same_agents_block_without_colliding(tmp_path, master_file):
    gw = _gateway(tmp_path)
    for acct, model in ((X, "x/figures"), (Y, "y/figures")):
        token = _as(acct)
        try:
            gw._config_set({"patch": {"agents": {"figure-creator": {"model": model}}}})
        finally:
            accounts.current_account.reset(token)

    for acct, model in ((X, "x/figures"), (Y, "y/figures")):
        token = _as(acct)
        try:
            values = gw._config_get(None)["values"]
            assert values["agents"]["figure-creator"]["model"] == model
        finally:
            accounts.current_account.reset(token)


# ── the three refusals ───────────────────────────────────────────────────────────────────────
def test_a_machine_key_is_refused_by_name(tmp_path, master_file):
    gw = _gateway(tmp_path)
    token = _as(X)
    try:
        res = gw._config_set({"patch": {"model": "x/model", "port": 9999, "workspace": "/tmp"}})
    finally:
        accounts.current_account.reset(token)

    assert res["saved"] is False
    assert res["refused"] == ["port", "workspace"]
    assert "belong to the machine" in res["error"]
    # …and the acceptable half was NOT quietly written either — a refusal is all-or-nothing
    assert not account_config.overlay_path(gw.config, X).exists()


def test_provider_keys_and_agent_secrets_are_refused_for_an_account(tmp_path, master_file):
    """The .env sits beside the master and is read into ONE process environment. Writing a
    tenant's credential there would hand it to every other tenant's agents."""
    gw = _gateway(tmp_path)
    token = _as(X)
    try:
        res = gw._config_set({"keys": {"ANTHROPIC_API_KEY": "sk-mine"}})
    finally:
        accounts.current_account.reset(token)

    assert res["saved"] is False
    assert res["refused"] == ["ANTHROPIC_API_KEY"]


def test_raw_replaces_the_accounts_overlay_and_not_the_daemon_file(tmp_path, master_file):
    gw = _gateway(tmp_path)
    before = master_file.read_text(encoding="utf-8")
    token = _as(X)
    try:
        res = gw._config_set({"raw": json.dumps({"model": "x/raw", "reasoning_effort": "high"})})
    finally:
        accounts.current_account.reset(token)

    assert res["saved"] is True
    assert master_file.read_text(encoding="utf-8") == before
    stored = json.loads(account_config.overlay_path(gw.config, X).read_text(encoding="utf-8"))
    assert stored == {"model": "x/raw", "reasoning_effort": "high"}


def test_raw_carrying_a_machine_key_is_refused_by_name(tmp_path, master_file):
    gw = _gateway(tmp_path)
    token = _as(X)
    try:
        res = gw._config_set({"raw": json.dumps({"model": "x/raw", "state_dir": "/elsewhere"})})
    finally:
        accounts.current_account.reset(token)
    assert res["saved"] is False and res["refused"] == ["state_dir"]


def test_invalid_raw_json_is_reported_not_swallowed(tmp_path, master_file):
    gw = _gateway(tmp_path)
    token = _as(X)
    try:
        res = gw._config_set({"raw": "{nope"})
    finally:
        accounts.current_account.reset(token)
    assert res["saved"] is False and "invalid JSON" in res["error"]


# ── what the page is told ────────────────────────────────────────────────────────────────────
def test_config_get_is_account_scoped_and_names_the_machine_only_knobs(tmp_path, master_file):
    gw = _gateway(tmp_path)
    token = _as(X)
    try:
        payload = gw._config_get(None)
    finally:
        accounts.current_account.reset(token)

    assert payload["accountScoped"] is True
    assert payload["keysWritable"] is False
    assert "workspace" in payload["machineOnly"] and "model" not in payload["machineOnly"]
    assert Path(payload["path"]) == account_config.overlay_path(gw.config, X)
    assert "envPath" not in payload  # the machine's .env location is not theirs to learn


def test_the_advanced_editor_shows_the_accounts_own_overlay(tmp_path, master_file):
    gw = _gateway(tmp_path)
    token = _as(X)
    try:
        assert json.loads(gw._config_get(None)["raw"]) == {}  # never saved yet
        gw._config_set({"patch": {"model": "x/model"}})
        assert json.loads(gw._config_get(None)["raw"]) == {"model": "x/model"}
    finally:
        accounts.current_account.reset(token)


def test_a_desktop_page_is_not_told_it_is_account_scoped(tmp_path, master_file, monkeypatch):
    # A PERSONAL daemon: the autouse fixture makes every other test hosted, and "no account on a
    # hosted daemon" is a state the connection gate does not allow — so the desktop case has to
    # say so, or it asserts something about a machine that cannot exist.
    monkeypatch.setattr(accounts, "enabled", lambda: False)
    payload = _gateway(tmp_path)._config_get(None)
    assert payload["accountScoped"] is False
    assert payload["machineOnly"] == []
    assert payload["keysWritable"] is True
    assert "envPath" in payload


# ── desktop / signed-out: unchanged ──────────────────────────────────────────────────────────
def test_with_no_account_a_save_still_writes_the_master_and_hot_applies(
    tmp_path, master_file, monkeypatch
):
    """The single-user path must not have moved. Same file, same immediate effect."""
    monkeypatch.setattr(accounts, "enabled", lambda: False)  # a personal daemon
    gw = _gateway(tmp_path)
    res = gw._config_set({"patch": {"model": "desktop/model"}})

    assert res["saved"] is True
    assert Path(res["path"]) == master_file
    assert json.loads(master_file.read_text(encoding="utf-8"))["model"] == "desktop/model"
    assert gw.config.model == "desktop/model"  # hot-applied, as before
    # and no overlay was invented for a connection that has no account
    assert not (gw.config.state_dir / "accounts").exists()


def test_with_no_account_raw_still_replaces_the_whole_master(tmp_path, master_file, monkeypatch):
    monkeypatch.setattr(accounts, "enabled", lambda: False)
    gw = _gateway(tmp_path)
    res = gw._config_set({"raw": json.dumps({"model": "desktop/raw", "port": 9})})
    assert res["saved"] is True
    assert json.loads(master_file.read_text(encoding="utf-8")) == {"model": "desktop/raw", "port": 9}


# ── fail closed ──────────────────────────────────────────────────────────────────────────────
def test_a_hosted_daemon_refuses_a_master_write_even_with_no_account(tmp_path, master_file, monkeypatch):
    """Belt for the branch's braces. The account check answers "whose connection is this"; this
    answers "is this machine shared at all" — so an accountless connection on a hosted daemon
    cannot fall through to the desktop path and rewrite the deployment's config."""
    gw = _gateway(tmp_path)
    monkeypatch.setattr(accounts, "enabled", lambda: True)
    before = master_file.read_text(encoding="utf-8")

    res = gw._config_set({"patch": {"model": "sneaky/model"}})

    assert res["saved"] is False
    assert "server-side" in res["error"]
    assert master_file.read_text(encoding="utf-8") == before
    assert gw.config.model == "openai/gpt-5"


def test_a_signed_in_desktop_still_uses_the_machines_config(tmp_path, master_file, monkeypatch):
    """A desktop that signs into the platform carries an account — and must NOT be treated as a
    tenant. The machine is the person's own: their Settings keep writing the real config file and
    their own provider keys, exactly as before they signed in. Routing on the account alone would
    have quietly taken both away."""
    monkeypatch.setattr(accounts, "enabled", lambda: False)  # personal daemon, cloud sign-in
    gw = _gateway(tmp_path)
    token = _as(X)
    try:
        res = gw._config_set({"patch": {"model": "mine/model"}})
        payload = gw._config_get(None)
    finally:
        accounts.current_account.reset(token)

    assert Path(res["path"]) == master_file
    assert gw.config.model == "mine/model"
    assert payload["accountScoped"] is False
    assert payload["keysWritable"] is True  # their machine, their keys
    assert not (gw.config.state_dir / "accounts").exists()


# ── the deployment's own defaults (admins) ───────────────────────────────────────────────────
# The one write on a shared daemon that IS supposed to reach everybody. Its gate is identity
# rather than shape, so these tests are about WHO, and about the layering surviving the change:
# a user who overrode a key keeps their value, a user who did not moves with the default.
ADMIN_EMAIL = "boss@example.com"


def _as_admin(monkeypatch, email: str = ADMIN_EMAIL):
    monkeypatch.setenv("AGENTD_ADMIN_IDENTITIES", f"{email},someone-else@example.com")
    return accounts.current_account.set({"account_id": "acct_admin", "email": email})


def test_an_admin_changes_the_deployment_default_for_everyone(tmp_path, master_file, monkeypatch):
    gw = _gateway(tmp_path)
    token = _as_admin(monkeypatch)
    try:
        res = gw._config_set({"target": "master", "patch": {"model": "openai/gpt-6"}})
    finally:
        accounts.current_account.reset(token)

    assert res["saved"] is True and res["target"] == "master"
    assert json.loads(master_file.read_text(encoding="utf-8"))["model"] == "openai/gpt-6"
    assert gw.config.model == "openai/gpt-6"  # hot-applied: this one is meant to reach everybody

    token = _as(X)  # a user who never chose a model follows the new default
    try:
        assert gw._config_get(None)["values"]["model"] == "openai/gpt-6"
    finally:
        accounts.current_account.reset(token)


def test_a_users_own_choice_survives_an_admin_changing_the_default(tmp_path, master_file, monkeypatch):
    """The whole point of layering. If an admin edit overwrote personal settings, nobody could
    ever safely change a default while people were connected."""
    gw = _gateway(tmp_path)
    token = _as(X)
    try:
        gw._config_set({"patch": {"model": "x/keeps-this"}})
    finally:
        accounts.current_account.reset(token)

    token = _as_admin(monkeypatch)
    try:
        gw._config_set({"target": "master", "patch": {"model": "openai/gpt-6"}})
    finally:
        accounts.current_account.reset(token)

    token = _as(X)
    try:
        assert gw._config_get(None)["values"]["model"] == "x/keeps-this"
    finally:
        accounts.current_account.reset(token)


def test_a_master_edit_is_visible_to_already_connected_accounts(tmp_path, master_file, monkeypatch):
    """The cache clear. Effective configs are derived from the master and cached per account
    against the OVERLAY's mtime — which a master edit does not touch. Without the clear, an admin
    changes a default and watches nothing happen for anyone already connected."""
    gw = _gateway(tmp_path)
    token = _as(X)
    try:
        assert gw._config_get(None)["values"]["model"] == "openai/gpt-5"  # warms X's cache
    finally:
        accounts.current_account.reset(token)

    token = _as_admin(monkeypatch)
    try:
        gw._config_set({"target": "master", "patch": {"model": "openai/gpt-6"}})
    finally:
        accounts.current_account.reset(token)

    token = _as(X)
    try:
        assert gw._config_get(None)["values"]["model"] == "openai/gpt-6"
    finally:
        accounts.current_account.reset(token)


def test_a_non_admin_cannot_touch_the_deployment_defaults(tmp_path, master_file, monkeypatch):
    monkeypatch.setenv("AGENTD_ADMIN_IDENTITIES", ADMIN_EMAIL)
    gw = _gateway(tmp_path)
    before = master_file.read_text(encoding="utf-8")
    token = accounts.current_account.set({"account_id": X, "email": "nobody@example.com"})
    try:
        res = gw._config_set({"target": "master", "patch": {"model": "sneaky/model"}})
    finally:
        accounts.current_account.reset(token)

    assert res["saved"] is False and "administrator" in res["error"]
    assert master_file.read_text(encoding="utf-8") == before
    assert gw.config.model == "openai/gpt-5"


def test_with_no_admins_named_nobody_can_edit_the_defaults(tmp_path, master_file, monkeypatch):
    """An empty list means nobody — a deployment that never named an admin cannot have its config
    edited over the wire at all, rather than defaulting to 'everyone'."""
    monkeypatch.setenv("AGENTD_ADMIN_IDENTITIES", "")
    gw = _gateway(tmp_path)
    token = accounts.current_account.set({"account_id": "acct_admin", "email": ADMIN_EMAIL})
    try:
        res = gw._config_set({"target": "master", "patch": {"model": "x"}})
    finally:
        accounts.current_account.reset(token)
    assert res["saved"] is False


def test_an_admin_may_not_replace_the_whole_file_or_write_secrets(tmp_path, master_file, monkeypatch):
    gw = _gateway(tmp_path)
    token = _as_admin(monkeypatch)
    try:
        raw_res = gw._config_set({"target": "master", "raw": json.dumps({"model": "x"})})
        key_res = gw._config_set({"target": "master", "keys": {"ANTHROPIC_API_KEY": "sk"}})
    finally:
        accounts.current_account.reset(token)
    assert raw_res["saved"] is False and "whole config file" in raw_res["error"]
    assert key_res["saved"] is False


def test_an_admin_reading_master_sees_the_deployment_not_their_own_overrides(tmp_path, master_file, monkeypatch):
    """An admin is a user too. If the defaults editor showed their personal value, they would
    edit the wrong number and not find out until somebody else complained."""
    gw = _gateway(tmp_path)
    token = _as_admin(monkeypatch)
    try:
        gw._config_set({"patch": {"model": "admins/own-preference"}})  # their personal setting
        personal = gw._config_get(None)
        deployment = gw._config_get(None, {"target": "master"})
    finally:
        accounts.current_account.reset(token)

    assert personal["values"]["model"] == "admins/own-preference"
    assert personal["accountScoped"] is True and personal["isAdmin"] is True
    assert deployment["values"]["model"] == "openai/gpt-5"
    assert deployment["target"] == "master" and deployment["accountScoped"] is False
    assert "raw" not in deployment  # no editor we cannot honour


def test_a_non_admin_asking_for_master_just_gets_their_own(tmp_path, master_file, monkeypatch):
    monkeypatch.setenv("AGENTD_ADMIN_IDENTITIES", ADMIN_EMAIL)
    gw = _gateway(tmp_path)
    token = accounts.current_account.set({"account_id": X, "email": "nobody@example.com"})
    try:
        payload = gw._config_get(None, {"target": "master"})
    finally:
        accounts.current_account.reset(token)
    assert payload["isAdmin"] is False
    assert payload["accountScoped"] is True and payload["target"] == "account"


# ── declared agent settings: an account's save has a store now ───────────────────────────────


def _gateway_with_declaring_agent(tmp_path) -> Gateway:
    """A gateway whose registry holds ONE agent declaring COMFYUI_URL — the user-supplied-host
    shape the per-account settings store exists for."""
    from agent_runtime.domain.agent import SettingField

    gw = _gateway(tmp_path)
    spec = SimpleNamespace(
        id="comfy",
        settings=(SettingField(key="COMFYUI_URL", kind="url"),),
        mcp=(),
    )
    gw.registry = SimpleNamespace(
        get=lambda aid: spec if aid == "comfy" else (_ for _ in ()).throw(KeyError(aid)),
        list_ids=lambda: ["comfy"],
    )
    return gw


def test_an_account_save_of_a_declared_setting_lands_in_the_per_account_store(
    tmp_path, master_file
):
    """THE WEB SAVE. This path used to refuse every `keys` write with "per-account secrets need
    their own store" — a message that outlived the store being built. The refusal meant a hosted
    user's ComfyUI URL silently vanished on Save while the desktop path stored it happily.
    """
    from agent_runtime.infrastructure.account_settings import AccountSettingsStore

    gw = _gateway_with_declaring_agent(tmp_path)
    token = _as(X)
    try:
        res = gw._config_set({"keys": {"COMFYUI_URL": "http://10.0.0.5:8188"}, "agentId": "comfy"})
    finally:
        accounts.current_account.reset(token)

    assert res["saved"] is True, res
    assert res["keysApplied"] == ["COMFYUI_URL"]
    stored = AccountSettingsStore(tmp_path / "state").read(X, "comfy")
    assert stored.get("COMFYUI_URL") == "http://10.0.0.5:8188"


def test_two_accounts_store_two_different_hosts_for_one_agent(tmp_path, master_file):
    """The point of the store: one shared daemon, one agent, and each account's URL is its own."""
    from agent_runtime.infrastructure.account_settings import AccountSettingsStore

    gw = _gateway_with_declaring_agent(tmp_path)
    for acct, url in ((X, "http://alice.example:8188"), (Y, "http://bob.example:8188")):
        token = _as(acct)
        try:
            gw._config_set({"keys": {"COMFYUI_URL": url}, "agentId": "comfy"})
        finally:
            accounts.current_account.reset(token)

    store = AccountSettingsStore(tmp_path / "state")
    assert store.read(X, "comfy")["COMFYUI_URL"] == "http://alice.example:8188"
    assert store.read(Y, "comfy")["COMFYUI_URL"] == "http://bob.example:8188"


def test_a_provider_key_is_still_refused_while_the_declared_setting_saves(tmp_path, master_file):
    """The refusal narrowed, it did not vanish: the machine's .env is still shared, so a provider
    key from an account is still a leak — but it must not take the agent's own settings down
    with it."""
    gw = _gateway_with_declaring_agent(tmp_path)
    token = _as(X)
    try:
        res = gw._config_set(
            {
                "keys": {"COMFYUI_URL": "http://10.0.0.5:8188", "ANTHROPIC_API_KEY": "sk-mine"},
                "agentId": "comfy",
            }
        )
    finally:
        accounts.current_account.reset(token)

    assert res["keysApplied"] == ["COMFYUI_URL"]
    assert "ANTHROPIC_API_KEY" in res["refused"]
    assert res["saved"] is True  # the half that could save, did — and the answer says which

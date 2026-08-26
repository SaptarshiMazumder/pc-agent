"""`[[settings]]` — what an agent declares it needs from whoever runs it.

Two things are being pinned here, and they are the same thing seen from two sides.

  THE DECLARATION SHIPS, THE VALUE DOES NOT.  `agent.toml` travels inside the package, so an
  author says "my agent needs COINBASE_API_KEY" and every installer sees a field for it. The
  values live in the installer's own `.env`, which was never packaged. A secret is write-only
  over the wire — presence, never the string.

  A DECLARATION IS A PERMISSION.  `config.set` used to write ANY env name: a settings page that
  shipped inside somebody else's download could overwrite ANTHROPIC_API_KEY and bill their turns
  to a stranger. The allowlist is now the provider keys plus exactly what this agent declared,
  which is why the two land in one test file — the declaration is what makes the allowlist
  possible.
"""

import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from agent_runtime.presentation import gateway as gateway_module

from agent_runtime.domain.agent import SettingField
from agent_runtime.infrastructure.agents.file_registry import FileAgentRegistry, _settings_fields
from agent_runtime.presentation.gateway import PROVIDER_ENV_KEYS, Gateway

DECLARING = """
name = "Trader"

[[settings]]
key      = "COINBASE_API_KEY"
label    = "Coinbase API key"
kind     = "secret"
required = true
help     = "Read-only key from Settings -> API."

[[settings]]
key   = "TRADING_DB_URL"
label = "Database URL"
kind  = "url"
"""


def _agents_dir(tmp_path, **agents: str) -> Path:
    """An agents/ tree. Every key becomes <id>/agent.toml with the given text."""
    root = tmp_path / "agents"
    for agent_id, toml in agents.items():
        d = root / agent_id
        d.mkdir(parents=True)
        (d / "agent.toml").write_text(toml, encoding="utf-8")
    return root


def _registry(tmp_path, **agents: str) -> FileAgentRegistry:
    root = _agents_dir(tmp_path, **agents)
    return FileAgentRegistry(SimpleNamespace(agents_dir=root, state_dir=tmp_path / "state"))


def _installed(tmp_path, *agent_ids: str) -> None:
    """Mark agents as having arrived in a .agentpkg — the line between code the user wrote and
    code they downloaded, which is what the patch allowlist keys off."""
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "installed_bundles.json").write_text(
        json.dumps({"bundles": [{"id": a} for a in agent_ids]}), encoding="utf-8"
    )


def _gateway(tmp_path, registry=None) -> Gateway:
    cfg = SimpleNamespace(
        state_dir=tmp_path / "state",
        agent_id="main",
        agent_name="jarvis",
        accounts={},
        distribution=None,
        model_proxy={},
        model_gateway={},
        mcp_servers=[],
    )
    return Gateway(config=cfg, service=None, registry=registry)


# ── the parser ──────────────────────────────────────────────────────────────
def test_a_declared_field_survives_with_every_attribute():
    (f,) = _settings_fields([{"key": "K", "label": "L", "kind": "secret", "required": True, "help": "H"}])
    assert (f.key, f.label, f.kind, f.required, f.help) == ("K", "L", "secret", True, "H")
    assert f.secret is True


def test_label_defaults_to_the_key_so_a_field_is_never_nameless():
    (f,) = _settings_fields([{"key": "TRADING_DB_URL"}])
    assert f.label == "TRADING_DB_URL" and f.kind == "text" and f.required is False


def test_a_row_with_no_key_is_dropped_and_logged(caplog):
    """Dropped because there is no env name to write; LOGGED because the author has to hear
    about it from the daemon, not from a buyer whose settings page is missing a field."""
    with caplog.at_level(logging.WARNING, logger="agentd"):
        assert _settings_fields([{"label": "no key"}, {"key": "OK"}], "demo") == (
            SettingField(key="OK", label="OK"),
        )
    assert "no `key`" in caplog.text and "demo" in caplog.text


def test_a_malformed_row_never_takes_the_whole_agent_down(caplog):
    """One typo in an optional block must not stop an agent whose every other field is fine."""
    with caplog.at_level(logging.WARNING, logger="agentd"):
        out = _settings_fields(["not a table", {"key": "GOOD"}], "demo")
    assert [f.key for f in out] == ["GOOD"]
    assert "not a table" in caplog.text


def test_an_unknown_kind_becomes_text_not_a_secret(caplog):
    """Wrong toward text: a typo'd `secrret` shown in a plain box is the same outcome as a text
    field. Wrong the other way merely hides a value that never needed hiding."""
    with caplog.at_level(logging.WARNING, logger="agentd"):
        (f,) = _settings_fields([{"key": "K", "kind": "secrret"}], "demo")
    assert f.kind == "text" and f.secret is False
    assert "unknown kind" in caplog.text


def test_the_first_declaration_of_a_key_wins(caplog):
    with caplog.at_level(logging.WARNING, logger="agentd"):
        out = _settings_fields([{"key": "K", "label": "first"}, {"key": "K", "label": "second"}])
    assert [f.label for f in out] == ["first"]
    assert "twice" in caplog.text


def test_no_settings_block_is_the_normal_case():
    assert _settings_fields(None) == () and _settings_fields([]) == ()


# ── the registry ────────────────────────────────────────────────────────────
def test_the_registry_reads_the_block_off_disk(tmp_path):
    spec = _registry(tmp_path, trader=DECLARING).get("trader")
    assert [(f.key, f.kind, f.required) for f in spec.settings] == [
        ("COINBASE_API_KEY", "secret", True),
        ("TRADING_DB_URL", "url", False),
    ]


def test_an_agent_that_declares_nothing_has_no_settings(tmp_path):
    assert _registry(tmp_path, plain='name = "Plain"\n').get("plain").settings == ()


# ── config.get: the shape, never the secret ─────────────────────────────────
def test_config_get_returns_the_declared_fields(tmp_path):
    payload = _gateway(tmp_path, _registry(tmp_path, trader=DECLARING))._config_get("trader")
    assert payload["settings"] == [
        {
            "key": "COINBASE_API_KEY",
            "label": "Coinbase API key",
            "kind": "secret",
            "required": True,
            "help": "Read-only key from Settings -> API.",
        },
        {"key": "TRADING_DB_URL", "label": "Database URL", "kind": "url", "required": False, "help": ""},
    ]


def test_a_secret_is_presence_only_while_a_url_is_readable(tmp_path, monkeypatch):
    """The user has to be able to fix a typo'd URL without retyping it. A key they cannot see
    is merely inconvenient; a key the page can read back is exfiltration."""
    monkeypatch.setenv("trader__COINBASE_API_KEY", "sk-live-do-not-leak")
    monkeypatch.setenv("trader__TRADING_DB_URL", "postgres://db/trades")
    payload = _gateway(tmp_path, _registry(tmp_path, trader=DECLARING))._config_get("trader")

    assert payload["env"]["COINBASE_API_KEY"] is True
    assert payload["settingsValues"] == {"TRADING_DB_URL": "postgres://db/trades"}
    assert "sk-live-do-not-leak" not in repr(payload)


def test_the_page_never_sees_the_prefix(tmp_path, monkeypatch):
    """Storage is prefixed; the conversation with the page is in the author's own terms."""
    monkeypatch.setenv("trader__TRADING_DB_URL", "postgres://db/trades")
    payload = _gateway(tmp_path, _registry(tmp_path, trader=DECLARING))._config_get("trader")
    assert [f["key"] for f in payload["settings"]] == ["COINBASE_API_KEY", "TRADING_DB_URL"]
    assert "trader__TRADING_DB_URL" not in repr(payload)


def test_another_agents_value_is_not_this_agents_value(tmp_path, monkeypatch):
    """THE POINT OF THE PREFIX. Two agents, one machine, the same declared name, two accounts."""
    monkeypatch.setenv("trader__TRADING_DB_URL", "postgres://db/mine")
    monkeypatch.setenv("other__TRADING_DB_URL", "postgres://db/theirs")
    registry = _registry(tmp_path, trader=DECLARING, other=DECLARING)
    gw = _gateway(tmp_path, registry)
    assert gw._config_get("trader")["settingsValues"]["TRADING_DB_URL"] == "postgres://db/mine"
    assert gw._config_get("other")["settingsValues"]["TRADING_DB_URL"] == "postgres://db/theirs"


def test_the_daemons_own_variable_is_not_mistaken_for_the_agents(tmp_path, monkeypatch):
    """An unfilled setting reads as EMPTY, never as whatever the daemon happens to export under
    that name — the silent-wrong-account failure this scheme exists to stop."""
    monkeypatch.setenv("TRADING_DB_URL", "postgres://db/the-daemons-own")
    monkeypatch.delenv("trader__TRADING_DB_URL", raising=False)
    payload = _gateway(tmp_path, _registry(tmp_path, trader=DECLARING))._config_get("trader")
    assert payload["env"]["TRADING_DB_URL"] is False
    assert payload["settingsValues"]["TRADING_DB_URL"] == ""


def test_a_host_connection_declares_nothing(tmp_path):
    """No scope names no agent, so there is no author whose declarations would apply."""
    payload = _gateway(tmp_path, _registry(tmp_path, trader=DECLARING))._config_get(None)
    assert payload["settings"] == [] and payload["settingsValues"] == {}


def test_a_scoped_page_whose_agent_is_gone_raises(tmp_path):
    """Rendering it as "declares nothing" is the one reading under which the bug is invisible."""
    gw = _gateway(tmp_path, _registry(tmp_path, trader=DECLARING))
    with pytest.raises(RuntimeError, match="unknown agent: ghost"):
        gw._config_get("ghost")


# ── config.set: the declaration is the permission ───────────────────────────
def _capture_env(monkeypatch, tmp_path) -> dict:
    """Swap the real ~/.agentd/.env for a recorder — a test must never write the user's keys."""
    written: dict = {}

    class _Env:
        path = tmp_path / ".env"

        def update(self, values):  # EnvFile.update -> True when the file was written
            written.update(values)
            return True

    monkeypatch.setattr("agent_runtime.presentation.gateway._user_env_file", lambda: _Env())
    return written


def _stored(tmp_path, agent_id: str) -> dict:
    """What landed in the agent's own config file."""
    import json

    f = tmp_path / "agents" / agent_id / "agent.config.json"
    return json.loads(f.read_text(encoding="utf-8")).get("settings", {}) if f.is_file() else {}


def test_a_declared_setting_lands_in_the_agents_own_config(tmp_path, monkeypatch):
    """WHERE A SETTING LIVES, and it moved.

    It used to be a line in the machine's shared `.env`, under a prefixed name
    (`trader__COINBASE_API_KEY`) invented precisely because one file was being shared by every
    agent on the machine. It is the agent's own `agent.config.json` now — so it travels with the
    agent, and inside its own file there is nothing to collide with, which is why the prefix is
    gone from the stored key.

    The `.env` is left alone entirely: a declared setting is one agent's, and the machine's file
    is for the credential every agent shares."""
    written = _capture_env(monkeypatch, tmp_path)
    gw = _gateway(tmp_path, _registry(tmp_path, trader=DECLARING))

    res = gw._config_set({"keys": {"COINBASE_API_KEY": "sk-user-own"}}, "trader")

    assert res["saved"] is True
    assert _stored(tmp_path, "trader") == {"COINBASE_API_KEY": "sk-user-own"}
    assert written == {}, "a declared setting must not reach the machine's .env"


def test_the_value_is_live_in_this_process_immediately(tmp_path, monkeypatch):
    """WRITING THE FILE IS NOT ENOUGH. Everything that consumes one of these reads an environment
    variable — a sandboxed tool is a child process, an [[mcp]] command is spawned with ${VAR}
    substituted — so a save that only wrote the file would take effect on the next daemon start.
    Somebody who pastes a URL and presses Test expects the test to use it.

    PREFIXED HERE, unprefixed in the file: one process environment cannot hold two agents'
    `AWS_ACCESS_KEY_ID`, and their two config files can."""
    import os

    _capture_env(monkeypatch, tmp_path)
    gw = _gateway(tmp_path, _registry(tmp_path, trader=DECLARING))
    monkeypatch.delenv("trader__COINBASE_API_KEY", raising=False)

    gw._config_set({"keys": {"COINBASE_API_KEY": "sk-user-own"}}, "trader")

    assert os.environ["trader__COINBASE_API_KEY"] == "sk-user-own"


def test_clearing_a_setting_removes_it_from_both(tmp_path, monkeypatch):
    """An empty value has always meant "unset" on this page, and in `.env` it deleted the line.
    Storing "" would be a third state — set, to nothing — that nothing in the UI can express."""
    import os

    _capture_env(monkeypatch, tmp_path)
    gw = _gateway(tmp_path, _registry(tmp_path, trader=DECLARING))
    gw._config_set({"keys": {"COINBASE_API_KEY": "sk-user-own"}}, "trader")

    gw._config_set({"keys": {"COINBASE_API_KEY": ""}}, "trader")

    assert _stored(tmp_path, "trader") == {}
    assert "trader__COINBASE_API_KEY" not in os.environ


def test_provider_keys_stay_writable_because_byok_is_the_point(tmp_path, monkeypatch):
    written = _capture_env(monkeypatch, tmp_path)
    gw = _gateway(tmp_path, _registry(tmp_path, trader=DECLARING))
    assert gw._config_set({"keys": {PROVIDER_ENV_KEYS[0]: "sk-byok"}}, "trader")["saved"] is True
    assert written == {PROVIDER_ENV_KEYS[0]: "sk-byok"}


def test_an_undeclared_name_is_refused_and_nothing_is_written(tmp_path, monkeypatch):
    """The hole this closes: a page that shipped inside a download writing a name of its own
    choosing. Refused LOUDLY — the author has to be able to see they forgot to declare it."""
    written = _capture_env(monkeypatch, tmp_path)
    gw = _gateway(tmp_path, _registry(tmp_path, trader=DECLARING))
    res = gw._config_set({"keys": {"AGENTD_STATE_DIR": "/somewhere/else"}}, "trader")
    assert res["saved"] is False
    assert res["refused"] == ["AGENTD_STATE_DIR"] and "AGENTD_STATE_DIR" in res["error"]
    assert written == {}


def test_one_bad_name_refuses_the_whole_write(tmp_path, monkeypatch):
    """Half-saving is worse than refusing: the page reports failure while some values landed."""
    written = _capture_env(monkeypatch, tmp_path)
    gw = _gateway(tmp_path, _registry(tmp_path, trader=DECLARING))
    res = gw._config_set({"keys": {"COINBASE_API_KEY": "ok", "PATH": "/evil"}}, "trader")
    assert res["saved"] is False and written == {}


def test_one_agent_may_not_write_another_agents_declared_key(tmp_path, monkeypatch):
    written = _capture_env(monkeypatch, tmp_path)
    registry = _registry(tmp_path, trader=DECLARING, plain='name = "Plain"\n')
    res = _gateway(tmp_path, registry)._config_set({"keys": {"COINBASE_API_KEY": "x"}}, "plain")
    assert res["saved"] is False and written == {}


def test_a_host_write_must_name_the_agent(tmp_path, monkeypatch):
    """A declared key belongs to an agent, not to the machine. Guessing would be right today and
    silently wrong the day a second agent declares the same name."""
    written = _capture_env(monkeypatch, tmp_path)
    gw = _gateway(tmp_path, _registry(tmp_path, trader=DECLARING))

    res = gw._config_set({"keys": {"TRADING_DB_URL": "postgres://x"}}, None)
    assert res["saved"] is False and res["ambiguous"] == ["TRADING_DB_URL"]
    assert written == {}

    res = gw._config_set({"keys": {"TRADING_DB_URL": "postgres://x"}, "agentId": "trader"}, None)
    # Named, so it is no longer ambiguous — and it lands in THAT agent's own config, which is
    # also why naming matters: two agents' files are two places, and picking the wrong one writes
    # a credential into the wrong agent's slot.
    assert res["saved"] is True and _stored(tmp_path, "trader") == {"TRADING_DB_URL": "postgres://x"}


def test_cloud_mode_locks_provider_keys_but_not_an_agents_own_settings(tmp_path, monkeypatch):
    """Which model pays for a turn has NO bearing on whether someone may store a third-party
    credential on their own machine. The lock used to sit in front of the whole payload, so
    signing in and choosing Cloud silently threw away a user's AWS key: the save reported
    nothing, the field stayed "not set", and no part of the UI could explain it."""
    written = _capture_env(monkeypatch, tmp_path)
    gw = _gateway(tmp_path, _registry(tmp_path, trader=DECLARING))
    monkeypatch.setattr(gw, "_platform_keys_locked", lambda: True)

    res = gw._config_set({"keys": {"COINBASE_API_KEY": "sk-mine"}}, "trader")
    # It lands in the AGENT's own config now, not the machine's .env — see
    # test_a_declared_setting_lands_in_the_agents_own_config.
    assert res["saved"] is True and _stored(tmp_path, "trader") == {"COINBASE_API_KEY": "sk-mine"}

    res = gw._config_set({"keys": {PROVIDER_ENV_KEYS[0]: "sk-nope"}}, "trader")
    assert res["saved"] is False and res["refused"] == [PROVIDER_ENV_KEYS[0]]
    assert PROVIDER_ENV_KEYS[0] not in written


def test_local_mode_still_takes_provider_keys(tmp_path, monkeypatch):
    written = _capture_env(monkeypatch, tmp_path)
    gw = _gateway(tmp_path, _registry(tmp_path, trader=DECLARING))
    monkeypatch.setattr(gw, "_platform_keys_locked", lambda: False)
    assert gw._config_set({"keys": {PROVIDER_ENV_KEYS[0]: "sk-byok"}}, "trader")["saved"] is True
    assert written == {PROVIDER_ENV_KEYS[0]: "sk-byok"}


def test_a_host_still_cannot_invent_an_env_name(tmp_path, monkeypatch):
    written = _capture_env(monkeypatch, tmp_path)
    gw = _gateway(tmp_path, _registry(tmp_path, trader=DECLARING))
    assert gw._config_set({"keys": {"PATH": "/evil"}}, None)["saved"] is False
    assert written == {}


# ── config.set patch: a DOWNLOADED page is not the machine's control panel ──
def _patch_recorder(monkeypatch) -> list:
    seen: list = []
    monkeypatch.setattr(
        gateway_module,
        "_persist_config_patch",
        lambda clean: (seen.append(clean), (True, "cfg.json"))[1],
    )
    return seen


def test_an_installed_page_may_edit_its_own_block(tmp_path, monkeypatch):
    seen = _patch_recorder(monkeypatch)
    _installed(tmp_path, "trader")
    gw = _gateway(tmp_path, _registry(tmp_path, trader=DECLARING))
    assert gw._config_set({"patch": {"agents": {"trader": {"model": "x"}}}}, "trader")["saved"] is True
    assert seen == [{"agents": {"trader": {"model": "x"}}}]


def test_an_installed_page_may_not_edit_another_agents_block(tmp_path, monkeypatch):
    seen = _patch_recorder(monkeypatch)
    _installed(tmp_path, "trader", "other")
    gw = _gateway(tmp_path, _registry(tmp_path, trader=DECLARING, other=DECLARING))
    res = gw._config_set({"patch": {"agents": {"other": {"model": "x"}}}}, "trader")
    assert res["saved"] is False and res["refused"] == ["agents.other"]
    assert seen == []


def test_an_installed_page_may_not_touch_machine_plumbing(tmp_path, monkeypatch):
    """The live hole this closes: `mcp_workshop` is an exposed knob, so a page that shipped inside
    a download could switch on chat-driven `add_mcp` and then run whatever it liked. Same for
    state_dir, agents_dir and the sandbox flags."""
    seen = _patch_recorder(monkeypatch)
    _installed(tmp_path, "trader")
    gw = _gateway(tmp_path, _registry(tmp_path, trader=DECLARING))
    for knob, value in (
        ("mcp_workshop", True),
        ("state_dir", "/somewhere/else"),
        ("agents_dir", "/somewhere/else"),
        ("sandbox_untrusted_plugins", False),
    ):
        res = gw._config_set({"patch": {knob: value}}, "trader")
        assert res["saved"] is False and res["refused"] == [knob], knob
    assert seen == []


def test_a_locally_authored_page_keeps_the_whole_surface(tmp_path, monkeypatch):
    """The line is CODE THE USER DOWNLOADED, not "is this a scoped connection" — the same line
    config.get already draws for its secret-bearing fields. Agent Builder's own settings window is
    a scoped page that deliberately edits the daemon, and it is the user's own tool."""
    seen = _patch_recorder(monkeypatch)
    gw = _gateway(tmp_path, _registry(tmp_path, trader=DECLARING))  # no install ledger
    assert gw._config_set({"patch": {"mcp_workshop": True}}, "trader")["saved"] is True
    assert seen == [{"mcp_workshop": True}]


def test_the_host_keeps_the_whole_config_surface(tmp_path, monkeypatch):
    seen = _patch_recorder(monkeypatch)
    _installed(tmp_path, "trader")
    gw = _gateway(tmp_path, _registry(tmp_path, trader=DECLARING))
    assert gw._config_set({"patch": {"mcp_workshop": True}}, None)["saved"] is True
    assert seen == [{"mcp_workshop": True}]


def test_an_installed_page_cannot_replace_the_whole_config_file(tmp_path):
    """`raw` walks past every allowlist by definition — it replaces the file."""
    _installed(tmp_path, "trader")
    gw = _gateway(tmp_path, _registry(tmp_path, trader=DECLARING))
    res = gw._config_set({"raw": '{"mcp_workshop": true}'}, "trader")
    assert res["saved"] is False and "cannot replace" in res["error"]

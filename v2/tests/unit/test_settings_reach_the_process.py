"""A value in an agent's own config reaches everything that consumes one.

THE CHAIN, and why it has this shape. A declared setting is stored in the agent's own
`agent.config.json` — that is the change. But everything that CONSUMES one can only read an
environment variable:

    the sandbox fetch broker   substitutes `${NAME}` into an outbound request, host-side
    an [[mcp]] server          is spawned as a child process with `${VAR}` already substituted
    an [[oauth]] declaration   resolves the installer's own client id/secret
    a plugin                   reads os.environ directly

That was true before this change and is true after it. So the file is the STORE and the
environment is the TRANSPORT — which is the shape it always actually had, with `.env` merely
having been mistaken for the store.

This test walks the whole chain: a config file on disk, a registry load, and then the exact
lookup `current_setting_env` performs for a running tool.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_runtime.domain.agent import resolve_setting_env, setting_env_name
from agent_runtime.infrastructure.agents.file_registry import FileAgentRegistry

DECLARES = """name = "Comfy Smith"

[[settings]]
key  = "COMFY_URL"
kind = "url"

[[settings]]
key  = "COMFY_TOKEN"
kind = "secret"
"""


def _agent(tmp_path: Path, config: dict | None) -> FileAgentRegistry:
    root = tmp_path / "agents"
    d = root / "comfy-smith"
    d.mkdir(parents=True)
    (d / "agent.toml").write_text(DECLARES, encoding="utf-8")
    if config is not None:
        (d / "agent.config.json").write_text(json.dumps(config), encoding="utf-8")
    return FileAgentRegistry(SimpleNamespace(agents_dir=root, state_dir=tmp_path / "state"))


def test_a_stored_value_is_in_the_environment_after_a_load(tmp_path, monkeypatch):
    """THE WHOLE POINT. Loading the agent is what puts its settings where a tool can read them."""
    monkeypatch.delenv("comfy-smith__COMFY_URL", raising=False)
    reg = _agent(tmp_path, {"settings": {"COMFY_URL": "https://pod", "COMFY_TOKEN": "sk-live"}})

    reg.get("comfy-smith")

    import os

    assert os.environ["comfy-smith__COMFY_URL"] == "https://pod"
    assert os.environ["comfy-smith__COMFY_TOKEN"] == "sk-live"


def test_it_is_the_name_a_running_tool_actually_looks_up(tmp_path, monkeypatch):
    """`current_setting_env` is the one entry point both credential-substitution sites use — the
    sandbox fetch broker and the unsandboxed path. If the export wrote a name that did not match
    what they ask for, a plugin would 401 with its key sitting right there in the file."""
    monkeypatch.delenv("comfy-smith__COMFY_URL", raising=False)
    reg = _agent(tmp_path, {"settings": {"COMFY_URL": "https://pod"}})
    spec = reg.get("comfy-smith")

    import os

    declared = tuple(f.key for f in spec.settings)
    looked_up = resolve_setting_env("COMFY_URL", "comfy-smith", declared)

    assert looked_up == setting_env_name("comfy-smith", "COMFY_URL")
    assert os.environ[looked_up] == "https://pod"


def test_the_agents_own_config_beats_a_stale_env_line(tmp_path, monkeypatch):
    """THE MIGRATION WOULD OTHERWISE BE INERT. `.env` is loaded into the environment at boot, so a
    leftover `comfy-smith__COMFY_URL` line would keep winning and the value in the agent's own
    file would never be the one in force — while the settings page showed the new one."""
    monkeypatch.setenv("comfy-smith__COMFY_URL", "https://the-old-one")
    reg = _agent(tmp_path, {"settings": {"COMFY_URL": "https://the-new-one"}})

    reg.get("comfy-smith")

    import os

    assert os.environ["comfy-smith__COMFY_URL"] == "https://the-new-one"


def test_an_agent_with_no_config_leaves_the_environment_alone(tmp_path, monkeypatch):
    """Every agent that has not been migrated or configured yet. Its `.env` value must survive —
    that is the entire compatibility story."""
    monkeypatch.setenv("comfy-smith__COMFY_URL", "https://from-dot-env")
    reg = _agent(tmp_path, None)

    reg.get("comfy-smith")

    import os

    assert os.environ["comfy-smith__COMFY_URL"] == "https://from-dot-env"


def test_an_empty_stored_value_does_not_blank_an_existing_one(tmp_path, monkeypatch):
    """"Set, to nothing" is not a state the UI can express, so it must not be one the export can
    produce either — an empty entry means unset, and unset means "whatever else provides it"."""
    monkeypatch.setenv("comfy-smith__COMFY_URL", "https://from-dot-env")
    reg = _agent(tmp_path, {"settings": {"COMFY_URL": ""}})

    reg.get("comfy-smith")

    import os

    assert os.environ["comfy-smith__COMFY_URL"] == "https://from-dot-env"

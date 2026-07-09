import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agentd.application.tool_models import ConfigMissingError, brain_model


def test_reads_from_config_not_env(monkeypatch):
    # even with AGENTD_MODEL set, the layer uses config.model — models are CONFIG-ONLY
    monkeypatch.setenv("AGENTD_MODEL", "deepseek/deepseek-v4-pro")
    cfg = SimpleNamespace(
        config_path="/x/agentd.config.json", model="gemini/gemini-3.1-pro-preview"
    )
    assert brain_model(cfg) == "gemini/gemini-3.1-pro-preview"


def test_per_agent_override_wins():
    cfg = SimpleNamespace(config_path="/x/agentd.config.json", model="gemini/x")
    assert brain_model(cfg, "anthropic/claude-y") == "anthropic/claude-y"


def test_missing_config_raises():
    cfg = SimpleNamespace(config_path="", model="gemini/x")  # no file loaded
    with pytest.raises(ConfigMissingError):
        brain_model(cfg)


def test_config_present_but_no_model_raises():
    cfg = SimpleNamespace(config_path="/x/agentd.config.json", model="")
    with pytest.raises(ConfigMissingError):
        brain_model(cfg)


def test_load_config_sets_config_path():
    # the real load_config finds v2/agentd.config.json and records its path
    from agentd.config import load_config

    assert load_config().config_path.endswith("agentd.config.json")

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.config import load_config


def test_guardrail_defaults():
    c = load_config()
    assert c.tool_timeout_default == 300.0
    assert c.tool_retries_default == 0
    assert c.tool_overrides == {}
    assert c.llm_idle_timeout_seconds == 120.0
    assert c.llm_request_timeout_seconds == 600.0
    assert c.computer_call_timeout_seconds == 120.0


def test_env_scalar_overrides(monkeypatch):
    monkeypatch.setenv("AGENTD_TOOL_TIMEOUT", "42")
    monkeypatch.setenv("AGENTD_TOOL_RETRIES", "3")
    monkeypatch.setenv("AGENTD_LLM_IDLE_TIMEOUT", "30")
    monkeypatch.setenv("AGENTD_LLM_REQUEST_TIMEOUT", "90")
    monkeypatch.setenv("AGENTD_COMPUTER_CALL_TIMEOUT", "15")
    c = load_config()
    assert c.tool_timeout_default == 42.0 and c.tool_retries_default == 3
    assert c.llm_idle_timeout_seconds == 30.0 and c.llm_request_timeout_seconds == 90.0
    assert c.computer_call_timeout_seconds == 15.0


def test_tool_overrides_from_json(tmp_path, monkeypatch):
    cfgfile = tmp_path / "agentd.config.json"
    cfgfile.write_text(json.dumps({
        "tool_timeout_default": 250,
        "tool_overrides": {"computer": {"timeout_sec": 900}, "exec": {"timeout_sec": None}},
    }), encoding="utf-8")
    monkeypatch.setenv("AGENTD_CONFIG", str(cfgfile))
    c = load_config()
    assert c.tool_timeout_default == 250
    assert c.tool_overrides == {"computer": {"timeout_sec": 900}, "exec": {"timeout_sec": None}}

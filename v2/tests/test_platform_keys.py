"""Platform-keys mode: the model-gateway seam's enable rule + credential handling.

The rule under test (model_gateway.configure): URL = env > config > distribution profile;
ON when the env URL is present, or config opts in, or the URL came from a HOSTED FLAVOR's
distribution profile AND a key exists (i.e. the user signed in) — a signed-out hosted
desktop stays BYOK.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agentd.distribution import parse_profile
from agentd.infrastructure import accounts
from agentd.infrastructure.llm import model_gateway


def _config(mg=None, platform_url=""):
    profile = parse_profile(
        {"platform": {"model_gateway_url": platform_url}} if platform_url else {},
        source_path="x" if platform_url else "",
    )
    return SimpleNamespace(model_gateway=mg or {}, distribution=profile)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("AGENTD_MODEL_GATEWAY_URL", raising=False)
    monkeypatch.delenv("AGENTD_MODEL_GATEWAY_KEY", raising=False)
    yield
    model_gateway.configure(_config())  # leave the module seam OFF for other tests


def test_default_off():
    model_gateway.configure(_config())
    assert not model_gateway.enabled()
    kwargs = {"model": "gemini/gemini-2.5-flash"}
    assert model_gateway.apply(kwargs) == {"model": "gemini/gemini-2.5-flash"}


def test_env_url_enables(monkeypatch):
    monkeypatch.setenv("AGENTD_MODEL_GATEWAY_URL", "http://proxy:4000")
    monkeypatch.setenv("AGENTD_MODEL_GATEWAY_KEY", "sk-master")
    model_gateway.configure(_config())
    assert model_gateway.enabled() and model_gateway.status()["source"] == "env"
    kwargs = model_gateway.apply({"model": "deepseek/deepseek-chat"})
    assert kwargs["model"] == "litellm_proxy/deepseek/deepseek-chat"
    assert kwargs["api_base"] == "http://proxy:4000"
    assert kwargs["api_key"] == "sk-master"


def test_config_needs_enabled_flag():
    model_gateway.configure(_config(mg={"api_base": "http://proxy:4000"}))
    assert not model_gateway.enabled()
    model_gateway.configure(_config(mg={"api_base": "http://proxy:4000", "enabled": True}))
    assert model_gateway.enabled() and model_gateway.status()["source"] == "config"


def test_hosted_flavor_off_until_signed_in(monkeypatch):
    """The desktop story: the flavor names the gateway, but the seam only turns ON once
    platform.connect has persisted a session token (the env key)."""
    cfg = _config(platform_url="http://gateway.example:4000")
    model_gateway.configure(cfg)
    assert not model_gateway.enabled()  # signed out => BYOK

    monkeypatch.setenv("AGENTD_MODEL_GATEWAY_KEY", "sess_abc")  # platform.connect happened
    model_gateway.configure(cfg)
    assert model_gateway.enabled() and model_gateway.status()["source"] == "distribution"
    kwargs = model_gateway.apply({"model": "gemini/gemini-2.5-flash"})
    assert kwargs["api_key"] == "sess_abc"
    assert kwargs["api_base"] == "http://gateway.example:4000"


def test_apply_attributes_account_user(monkeypatch):
    """Cloud path: with an account pinned on the contextvar, apply() names it via the
    OpenAI `user` field so the proxy can attribute master-key calls."""
    monkeypatch.setenv("AGENTD_MODEL_GATEWAY_URL", "http://proxy:4000")
    model_gateway.configure(_config())
    token = accounts.set_account({"account_id": "acct_123"})
    try:
        kwargs = model_gateway.apply({"model": "m"})
        assert kwargs["user"] == "acct_123"
    finally:
        accounts.reset_account(token)


def test_status_never_leaks_key(monkeypatch):
    monkeypatch.setenv("AGENTD_MODEL_GATEWAY_URL", "http://proxy:4000")
    monkeypatch.setenv("AGENTD_MODEL_GATEWAY_KEY", "sess_secret")
    model_gateway.configure(_config())
    status = model_gateway.status()
    assert status["has_key"] is True
    assert "sess_secret" not in str(status.values())

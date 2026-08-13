"""Platform-keys mode: the model-proxy seam's enable rule + credential handling.

The rule under test (model_proxy.configure): URL = env > config > distribution profile;
ON when the env URL is present, or config opts in, or the URL came from a HOSTED FLAVOR's
distribution profile AND a key exists (i.e. the user signed in) — a signed-out hosted
desktop stays BYOK.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agent_runtime.distribution import parse_profile
from agent_runtime.infrastructure import accounts
from agent_runtime.infrastructure.llm import model_gateway, model_proxy


def _config(proxy=None, platform_url="", legacy=None):
    profile = parse_profile(
        {"platform": {"model_proxy_url": platform_url}} if platform_url else {},
        source_path="x" if platform_url else "",
    )
    return SimpleNamespace(
        model_proxy=proxy or {},
        model_gateway=legacy or {},
        distribution=profile,
    )


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("AGENTD_MODEL_GATEWAY_URL", raising=False)
    monkeypatch.delenv("AGENTD_MODEL_GATEWAY_KEY", raising=False)
    monkeypatch.delenv("AGENTD_MODEL_PROXY_URL", raising=False)
    monkeypatch.delenv("AGENTD_MODEL_PROXY_KEY", raising=False)
    yield
    model_proxy.configure(_config())  # leave the module seam OFF for other tests


def test_default_off():
    model_proxy.configure(_config())
    assert not model_proxy.enabled()
    kwargs = {"model": "gemini/gemini-2.5-flash"}
    assert model_proxy.apply(kwargs) == {"model": "gemini/gemini-2.5-flash"}


def test_env_url_enables(monkeypatch):
    monkeypatch.setenv("AGENTD_MODEL_PROXY_URL", "http://proxy:4000")
    monkeypatch.setenv("AGENTD_MODEL_PROXY_KEY", "sk-master")
    model_proxy.configure(_config())
    assert model_proxy.enabled() and model_proxy.status()["source"] == "env"
    kwargs = model_proxy.apply({"model": "deepseek/deepseek-chat"})
    assert kwargs["model"] == "litellm_proxy/deepseek/deepseek-chat"
    assert kwargs["api_base"] == "http://proxy:4000"
    assert kwargs["api_key"] == "sk-master"


def test_config_needs_enabled_flag():
    model_proxy.configure(_config(proxy={"api_base": "http://proxy:4000"}))
    assert not model_proxy.enabled()
    model_proxy.configure(_config(proxy={"api_base": "http://proxy:4000", "enabled": True}))
    assert model_proxy.enabled() and model_proxy.status()["source"] == "config"


def test_hosted_flavor_off_until_a_connection_signs_in(monkeypatch):
    """The desktop story: the flavor names the proxy, and the seam turns on for a CONNECTION that
    carries a session — not for the machine.

    It used to turn on once a session had been persisted as the env key, which made it one answer
    for every window: whoever pressed Cloud last decided who paid for everyone."""
    from agent_runtime.infrastructure import accounts

    monkeypatch.delenv("AGENTD_MODEL_PROXY_KEY", raising=False)
    model_proxy.configure(_config(platform_url="http://proxy.example:4000"))
    assert not model_proxy.enabled()  # nobody on this connection => BYOK

    token = accounts.set_account({"account_id": "a1", "session_token": "sess_abc"})
    try:
        assert model_proxy.enabled() and model_proxy.status()["source"] == "distribution"
        kwargs = model_proxy.apply({"model": "gemini/gemini-2.5-flash"})
        assert kwargs["api_key"] == "sess_abc", "the connection's own session pays"
        assert kwargs["api_base"] == "http://proxy.example:4000"
    finally:
        accounts.reset_account(token)


def test_apply_attributes_account_user(monkeypatch):
    """Cloud path: with an account pinned on the contextvar, apply() names it via the
    OpenAI `user` field so the proxy can attribute master-key calls."""
    monkeypatch.setenv("AGENTD_MODEL_PROXY_URL", "http://proxy:4000")
    model_proxy.configure(_config())
    token = accounts.set_account({"account_id": "acct_123"})
    try:
        kwargs = model_proxy.apply({"model": "m"})
        assert kwargs["user"] == "acct_123"
    finally:
        accounts.reset_account(token)


def test_status_never_leaks_key(monkeypatch):
    monkeypatch.setenv("AGENTD_MODEL_PROXY_URL", "http://proxy:4000")
    monkeypatch.setenv("AGENTD_MODEL_PROXY_KEY", "sess_secret")
    model_proxy.configure(_config())
    status = model_proxy.status()
    assert status["has_key"] is True
    assert "sess_secret" not in str(status.values())


def test_legacy_names_remain_read_only_fallbacks(monkeypatch):
    monkeypatch.setenv("AGENTD_MODEL_GATEWAY_URL", "http://legacy-proxy:4000")
    monkeypatch.setenv("AGENTD_MODEL_GATEWAY_KEY", "legacy-key")
    model_proxy.configure(_config())
    assert model_proxy.status()["source"] == "env"
    assert model_proxy.apply({"model": "m"})["api_base"] == "http://legacy-proxy:4000"

    # Existing imports continue to point at the same process-global seam.
    assert model_gateway.enabled()
    assert model_gateway.status() == model_proxy.status()

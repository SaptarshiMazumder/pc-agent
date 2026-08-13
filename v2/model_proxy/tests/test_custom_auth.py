"""Contract tests for ``custom_auth`` — the model_proxy service's auth + metering hooks.

These live WITH the service, not in v2/tests, because custom_auth imports litellm's PROXY
internals (``litellm.proxy._types``). They must run against this service's pinned
requirements.txt, not agentd's floating ``litellm`` (which carries no proxy extra).
Run them from this directory:
``pip install -r requirements.txt pytest pytest-asyncio && pytest`` — the same install the
`model-proxy` CI job performs.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_auth_module():
    # tests/ lives INSIDE the service, so the module is one level up.
    path = Path(__file__).resolve().parents[1] / "custom_auth.py"
    spec = importlib.util.spec_from_file_location("agentd_model_proxy_custom_auth", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_master_key_is_accepted_without_accounts(monkeypatch):
    auth = _load_auth_module()
    monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-infra")
    monkeypatch.delenv("ACCOUNTS_URL", raising=False)

    result = await auth.user_api_key_auth(SimpleNamespace(), "sk-infra")

    # LiteLLM normalizes/hash-wraps the credential in UserAPIKeyAuth; successful
    # construction is the contract, and the raw master key must not be exposed.
    assert result.api_key
    assert result.api_key != "sk-infra"


@pytest.mark.asyncio
async def test_session_token_is_resolved_to_an_account(monkeypatch):
    auth = _load_auth_module()
    monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-infra")
    monkeypatch.setenv("ACCOUNTS_URL", "http://accounts:4100")
    monkeypatch.setenv("ACCOUNTS_RESOLVE_TTL", "60")

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"account_id": "acct_123"}

    class Client:
        async def get(self, path, headers):
            assert path == "/resolve"
            assert headers == {"Authorization": "Bearer sess_abc"}
            return Response()

    monkeypatch.setattr(auth, "_accounts_client", lambda: Client())
    result = await auth.user_api_key_auth(SimpleNamespace(), "sess_abc")

    assert result.user_id == "acct_123"


def test_usage_fields_fall_back_to_nonzero_cost():
    auth = _load_auth_module()
    response = SimpleNamespace(usage={"prompt_tokens": 10, "completion_tokens": 5})

    model, input_tokens, output_tokens, cost, cached = auth._usage_fields(
        {"model": "provider/model"},
        response,
    )

    assert (model, input_tokens, output_tokens) == ("provider/model", 10, 5)
    assert cost > 0
    assert cached == 0  # no cache detail reported => 0, never a guess


def test_usage_fields_read_cached_tokens_when_reported():
    """Cache reads cost ~10% of a normal input token, so the cached share is the biggest lever
    on cost of goods — and agents carry very large, very stable system prompts. Providers
    disagree on where they report it, so both shapes are pinned here."""
    auth = _load_auth_module()

    openai_shape = SimpleNamespace(
        usage={
            "prompt_tokens": 1000,
            "completion_tokens": 50,
            "prompt_tokens_details": {"cached_tokens": 800},
        }
    )
    assert auth._usage_fields({"model": "m"}, openai_shape)[4] == 800

    anthropic_shape = SimpleNamespace(
        usage={"prompt_tokens": 1000, "completion_tokens": 50, "cache_read_input_tokens": 640}
    )
    assert auth._usage_fields({"model": "m"}, anthropic_shape)[4] == 640


class _BoomClient:
    """An accounts client whose every round trip fails — the outage under test."""

    def __init__(self):
        self.calls = 0

    async def get(self, *a, **k):
        import httpx

        self.calls += 1
        raise httpx.ConnectError("boom")


@pytest.mark.asyncio
async def test_an_accounts_blip_serves_the_stale_resolution(monkeypatch):
    """THE mid-run killer. A multi-minute agent run crosses the 60s resolve TTL, and one
    dropped round trip used to abort the whole run with 'account service unavailable'. A token
    that resolved moments ago is still that user's: retry once, then serve the expired cache
    entry (bounded by the grace window) instead of failing a paying customer's run."""
    import time

    auth = _load_auth_module()
    monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-infra")
    monkeypatch.setenv("ACCOUNTS_URL", "http://accounts.test")
    boom = _BoomClient()
    monkeypatch.setattr(auth, "_accounts_client", lambda: boom)
    # a resolution that EXPIRED 10s ago — inside the grace window
    auth._resolve_cache["sess_x"] = ("acct_1", time.time() - 10)

    result = await auth.user_api_key_auth(SimpleNamespace(), "sess_x")

    assert result.user_id == "acct_1"
    assert boom.calls == 2, "one immediate retry before falling back"


@pytest.mark.asyncio
async def test_a_never_resolved_token_still_fails_closed(monkeypatch):
    """Grace is a memory, not a bypass: with nothing safe to fall back to, an outage still
    refuses — an attacker cannot mint access by timing an Accounts blip."""
    auth = _load_auth_module()
    monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-infra")
    monkeypatch.setenv("ACCOUNTS_URL", "http://accounts.test")
    monkeypatch.setattr(auth, "_accounts_client", lambda: _BoomClient())

    with pytest.raises(Exception, match="account service unavailable"):
        await auth.user_api_key_auth(SimpleNamespace(), "sess_never_seen")

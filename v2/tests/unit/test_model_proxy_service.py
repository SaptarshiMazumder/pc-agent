"""Contract tests for the independently packaged ``v2/model_proxy`` service."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_auth_module():
    path = Path(__file__).resolve().parents[2] / "model_proxy" / "custom_auth.py"
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

    model, input_tokens, output_tokens, cost = auth._usage_fields(
        {"model": "provider/model"},
        response,
    )

    assert (model, input_tokens, output_tokens) == ("provider/model", 10, 5)
    assert cost > 0

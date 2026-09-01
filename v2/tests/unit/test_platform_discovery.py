"""One baked address resolves to every platform URL — and never breaks the daemon when it can't.

WHAT THIS PROTECTS. Builds used to bake four independent ALB hostnames. Those carry an
AWS-assigned suffix that changes on any destroy/recreate, so every release froze another stale
set — and the failure was silent: a client pointed at a dead-but-DNS-resolvable stack signs in
against a DIFFERENT accounts database, so the same email is a different account with different
credits. Nothing anywhere reports it.

So the tests here are mostly about the FALLBACK ladder, because that is where a discovery scheme
gets dangerous: it must prefer what the deployment says today, fall back to yesterday's cache,
then to the baked values, and never, under any failure, stop a daemon from starting.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent_runtime import config as config_mod
from agent_runtime.infrastructure import platform_discovery

DOC = {
    "issuer": "https://accounts.live.example:4100",
    "auth_url": "https://accounts.live.example:4100",
    "model_proxy_url": "https://models.live.example:4000",
    "ws_url": "wss://app.live.example",
    "providers": [{"id": "local", "label": "Email", "kind": "password"}],
    "token_auth": True,
}

BAKED_ACCOUNTS = "http://stale-elb.example:4100"
BAKED_PROXY = "http://stale-elb.example:4000"
PLATFORM = "https://platform.live.example:4100"


def _config(tmp_path, **profile):
    return SimpleNamespace(
        state_dir=str(tmp_path),
        accounts=None,
        model_proxy={},
        distribution=SimpleNamespace(
            platform_url=profile.pop("platform_url", PLATFORM),
            accounts_url=profile.pop("accounts_url", BAKED_ACCOUNTS),
            model_proxy_url=profile.pop("model_proxy_url", BAKED_PROXY),
            model_gateway_url="",
            **profile,
        ),
    )


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    platform_discovery.reset()
    for var in ("AGENTD_PLATFORM_URL", "AGENTD_ACCOUNTS_URL", "AGENTD_MODEL_PROXY_URL",
                "AGENTD_MODEL_GATEWAY_URL"):
        monkeypatch.delenv(var, raising=False)
    yield
    platform_discovery.reset()


def _serve(monkeypatch, doc=DOC, status=200, boom: Exception | None = None):
    """Stand in for the network. Returns a call counter so memoisation can be asserted."""
    calls = {"n": 0}

    def fake_get(url, timeout=0):  # noqa: ARG001
        calls["n"] += 1
        if boom is not None:
            raise boom
        return SimpleNamespace(status_code=status, json=lambda: doc)

    import httpx

    monkeypatch.setattr(httpx, "get", fake_get)
    return calls


def test_one_baked_url_resolves_every_address(monkeypatch, tmp_path):
    _serve(monkeypatch)
    cfg = _config(tmp_path)
    assert config_mod.accounts_api_base(cfg) == DOC["auth_url"]
    assert platform_discovery.field(cfg, "model_proxy_url") == DOC["model_proxy_url"]


def test_discovery_beats_the_baked_value(monkeypatch, tmp_path):
    """The whole point: what the deployment says today wins over what the installer froze."""
    _serve(monkeypatch)
    cfg = _config(tmp_path)
    resolved = config_mod.accounts_api_base(cfg)
    assert resolved == DOC["auth_url"] != BAKED_ACCOUNTS


def test_env_and_machine_config_still_win(monkeypatch, tmp_path):
    """Discovery must not override a deliberate local override, or a developer can no longer
    point one daemon at a local stack."""
    _serve(monkeypatch)
    monkeypatch.setenv("AGENTD_ACCOUNTS_URL", "http://127.0.0.1:4100")
    assert config_mod.accounts_api_base(_config(tmp_path)) == "http://127.0.0.1:4100"

    monkeypatch.delenv("AGENTD_ACCOUNTS_URL")
    cfg = _config(tmp_path)
    cfg.accounts = {"api_base": "http://machine-config:4100"}
    assert config_mod.accounts_api_base(cfg) == "http://machine-config:4100"


def test_unreachable_platform_falls_back_to_baked(monkeypatch, tmp_path):
    """Offline must degrade, never fail. A daemon has to start on a plane."""
    import httpx

    _serve(monkeypatch, boom=httpx.ConnectError("no route"))
    assert config_mod.accounts_api_base(_config(tmp_path)) == BAKED_ACCOUNTS


def test_a_bad_response_falls_back_to_baked(monkeypatch, tmp_path):
    _serve(monkeypatch, status=503)
    assert config_mod.accounts_api_base(_config(tmp_path)) == BAKED_ACCOUNTS


def test_cache_serves_when_the_platform_goes_down(monkeypatch, tmp_path):
    """Stale beats absent: yesterday's addresses are likelier right than the frozen ones."""
    _serve(monkeypatch)
    cfg = _config(tmp_path)
    assert config_mod.accounts_api_base(cfg) == DOC["auth_url"]

    platform_discovery.reset()  # new process, same disk
    import httpx

    _serve(monkeypatch, boom=httpx.ConnectError("down"))
    assert config_mod.accounts_api_base(_config(tmp_path)) == DOC["auth_url"]


def test_cache_from_a_different_platform_is_discarded(monkeypatch, tmp_path):
    """A re-pointed build must not keep talking to the old stack. This is the whole
    two-accounts-for-one-email failure, in cache form."""
    _serve(monkeypatch)
    config_mod.accounts_api_base(_config(tmp_path))

    platform_discovery.reset()
    import httpx

    _serve(monkeypatch, boom=httpx.ConnectError("down"))
    repointed = _config(tmp_path, platform_url="https://other-platform.example:4100")
    assert config_mod.accounts_api_base(repointed) == BAKED_ACCOUNTS, "served another stack's cache"


def test_resolution_is_memoised(monkeypatch, tmp_path):
    calls = _serve(monkeypatch)
    cfg = _config(tmp_path)
    for _ in range(5):
        config_mod.accounts_api_base(cfg)
        platform_discovery.field(cfg, "model_proxy_url")
    assert calls["n"] == 1, "boot-time seams must not each cost a round trip"


def test_no_platform_url_never_touches_the_network(monkeypatch, tmp_path):
    """Every install that predates discovery behaves EXACTLY as before — no fetch, no delay."""
    calls = _serve(monkeypatch)
    cfg = _config(tmp_path, platform_url="")
    assert config_mod.accounts_api_base(cfg) == BAKED_ACCOUNTS
    assert calls["n"] == 0


def test_env_can_repoint_the_platform(monkeypatch, tmp_path):
    _serve(monkeypatch)
    monkeypatch.setenv("AGENTD_PLATFORM_URL", "https://elsewhere.example:4100")
    cfg = _config(tmp_path, platform_url="")
    assert config_mod.accounts_api_base(cfg) == DOC["auth_url"]


def test_model_proxy_prefers_discovery_over_the_baked_url(monkeypatch, tmp_path):
    from agent_runtime.infrastructure.llm import model_proxy

    _serve(monkeypatch)
    model_proxy.configure(_config(tmp_path))
    status = model_proxy.status()
    assert status["api_base"] == DOC["model_proxy_url"]
    assert status["source"] == "discovery"


def test_discovery_failure_cannot_break_config_resolution(monkeypatch, tmp_path):
    """A discovery bug must never be able to take down address resolution — hence the blanket
    guard in config.platform_discovered."""
    monkeypatch.setattr(
        platform_discovery, "field", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    assert config_mod.accounts_api_base(_config(tmp_path)) == BAKED_ACCOUNTS


def test_cache_file_is_written_and_readable(monkeypatch, tmp_path):
    _serve(monkeypatch)
    config_mod.accounts_api_base(_config(tmp_path))
    cached = json.loads((tmp_path / "platform-discovery.json").read_text("utf-8"))
    assert cached["auth_url"] == DOC["auth_url"] and cached["_base"] == PLATFORM

"""Contract tests for the scheduled-jobs Lambda (plan item 3.8).

Twenty lines of infrastructure glue, with two properties worth pinning because both are
invisible when broken:

  * IT MUST NOT BECOME AN OPEN POST RELAY. The endpoint is data — it arrives in the Scheduler
    payload — so a mistyped or hostile `path` of "http://elsewhere/" would otherwise make a
    function holding the accounts internal key POST that key wherever it is told.
  * FAILURE MUST PROPAGATE. The only thing watching these jobs is the AWS/Lambda `Errors`
    metric (vendor-supplied, so free, unlike a custom EMF counter). An except-and-return
    would leave the alarm permanently OK while renewals silently stopped.

Loaded by path: it lives under infra/ as Lambda source, not as an importable package.
"""

from __future__ import annotations

import importlib.util
import json
import urllib.error
from pathlib import Path

import pytest

V2 = Path(__file__).resolve().parents[2]
HANDLER = V2 / "infra" / "modules" / "lambda" / "scheduled_jobs.py"


@pytest.fixture
def jobs(monkeypatch):
    # Read at import time, so they must be set before the module is loaded.
    monkeypatch.setenv("ACCOUNTS_URL", "http://accounts.agentd.local:4100")
    monkeypatch.setenv("ACCOUNTS_INTERNAL_KEY", "internal-key-under-test")
    spec = importlib.util.spec_from_file_location("scheduled_jobs_under_test", HANDLER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Response:
    """The subset of http.client.HTTPResponse the handler touches."""

    def __init__(self, payload: dict, status: int = 200) -> None:
        self.status = status
        self._body = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        return None


def test_it_calls_the_endpoint_the_schedule_names(jobs, monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        seen["method"] = request.get_method()
        seen["key"] = request.get_header("X-internal-key")
        return _Response({"ok": True, "grants_closed": 2})

    monkeypatch.setattr(jobs.urllib.request, "urlopen", fake_urlopen)

    out = jobs.handler({"job": "close-expired-credits", "path": "/ledger/close-expired"}, None)

    assert seen["url"] == "http://accounts.agentd.local:4100/ledger/close-expired"
    assert seen["method"] == "POST"
    assert seen["key"] == "internal-key-under-test"
    # The endpoint's own answer is returned verbatim: it is the audit trail of what the run did.
    assert out["result"]["grants_closed"] == 2


@pytest.mark.parametrize("path", ["http://evil.example/steal", "//evil.example/steal", "", "ledger"])
def test_it_refuses_anything_that_is_not_a_relative_path(jobs, monkeypatch, path):
    """A guard, not validation theatre: this function holds a credential that can read the whole
    ledger and mint credits, and urllib would happily POST it to an absolute URL."""
    def must_not_be_called(*_a, **_kw):  # pragma: no cover - the point is that it is not reached
        raise AssertionError("the handler made a request for a non-relative path")

    monkeypatch.setattr(jobs.urllib.request, "urlopen", must_not_be_called)
    with pytest.raises(ValueError, match="must start with"):
        jobs.handler({"job": "x", "path": path}, None)


def test_a_non_2xx_response_raises_so_the_errors_metric_moves(jobs, monkeypatch):
    def fake_urlopen(_request, timeout=None):
        raise urllib.error.HTTPError(
            "http://accounts.agentd.local:4100/subscriptions/renew-due", 401,
            "Unauthorized", {}, None,
        )

    monkeypatch.setattr(jobs.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(urllib.error.HTTPError):
        jobs.handler({"job": "subscription-renewals", "path": "/subscriptions/renew-due"}, None)


def test_an_unreachable_service_raises_rather_than_reporting_success(jobs, monkeypatch):
    """A rolled task or a lost security-group rule is a stopped billing clock, not a quiet
    no-op."""
    def fake_urlopen(_request, timeout=None):
        raise urllib.error.URLError("timed out")

    monkeypatch.setattr(jobs.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(urllib.error.URLError):
        jobs.handler({"job": "ledger-snapshot", "path": "/ledger/snapshot"}, None)

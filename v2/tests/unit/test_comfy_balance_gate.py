"""Comfy Penguin's balance gate: a blip fails open, a wrong answer never reads as a balance."""

from __future__ import annotations

import comfy_bridge
from agent_runtime.infrastructure.net.outbound import Response


def _gate(monkeypatch, response: Response, need: int = 18_092):
    calls = []

    def fake_fetch(url, **kw):
        calls.append((url, kw))
        return response

    monkeypatch.setattr(comfy_bridge, "fetch", fake_fetch)
    monkeypatch.setattr(comfy_bridge, "current_account_id", lambda: "acct_1")
    ok, why = comfy_bridge._affordable(need)
    return ok, why, calls


def test_it_reads_credits_not_the_dollar_budget(monkeypatch):
    ok, _, calls = _gate(monkeypatch, Response(status=200, text='{"credits_remaining": 9297407}'))
    assert ok
    assert "/credits/acct_1" in calls[0][0] and "/budget/" not in calls[0][0]


def test_too_few_credits_refuses(monkeypatch):
    ok, why, _ = _gate(monkeypatch, Response(status=200, text='{"credits_remaining": 100}'))
    assert not ok and "has 100" in why


def test_a_missing_credits_number_is_not_zero_and_not_fine(monkeypatch):
    # The old bug: /budget answered with no credits field, read as 0 -> "the account has 0".
    ok, why, _ = _gate(monkeypatch, Response(status=200, text='{"budget_usd": null, "spent_usd": 112.3}'))
    assert not ok and "balance check failed" in why and "has 0" not in why


def test_a_refused_credential_blocks_instead_of_failing_open(monkeypatch):
    # The hosted bug: a 401 read as "cannot tell, let it run" — and the debit after it 401'd too.
    ok, why, _ = _gate(monkeypatch, Response(status=401, text="missing bearer token"))
    assert not ok and "HTTP 401" in why


def test_an_accounts_outage_still_fails_open(monkeypatch):
    assert _gate(monkeypatch, Response(status=503))[0]
    assert _gate(monkeypatch, Response(error="ConnectError: refused"))[0]


def test_an_account_never_on_a_credit_plan_is_not_gated(monkeypatch):
    ok, _, _ = _gate(monkeypatch, Response(status=200, text='{"credits_remaining": 0, "credits_enforced": false}'))
    assert ok

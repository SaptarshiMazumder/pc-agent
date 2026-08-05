"""Contract tests for the ingest service (plan item 5.2).

This is the ONLY endpoint in the platform that accepts input from machines we do not control, so
these tests are about what a hostile client CANNOT do, not about the happy path:

  * invent a metric name — every name is a CloudWatch custom metric at $0.30/month, forever
  * invent a dimension value — dimensions multiply, so this is the same bill only worse
  * put content in a property — the client-side allowlist runs on THEIR machine and can be
    replaced, so this side has to re-check
  * claim someone else's account_id — that would poison every per-account signal
  * send a value that poisons a percentile, or a batch large enough to be a lever

The happy path is one test. The rest is the threat model.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

V2 = Path(__file__).resolve().parents[2]


@pytest.fixture
def ingest(monkeypatch, capsys):
    monkeypatch.setenv("AGENTD_SERVICE", "ingest")
    monkeypatch.setenv("AGENTD_TELEMETRY", "1")
    monkeypatch.setenv("INGEST_RATE_LIMIT", "0/0")  # off unless a test wants it
    monkeypatch.delenv("ACCOUNTS_URL", raising=False)
    spec = importlib.util.spec_from_file_location("agentd_ingest_app", V2 / "ingest" / "app.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with TestClient(module.app) as client:
        client.app_module = module  # type: ignore[attr-defined]
        yield client, capsys


def _emitted(capsys) -> list[dict]:
    """The EMF lines the service printed. stdout IS the pipeline, so this is the real output."""
    out = []
    for line in capsys.readouterr().out.splitlines():
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict) and "_aws" in parsed:
            out.append(parsed)
    return out


def _names(records: list[dict]) -> set[str]:
    return {m["Name"] for r in records for m in r["_aws"]["CloudWatchMetrics"][0]["Metrics"]}


def test_an_allowed_event_is_published_as_emf(ingest):
    client, capsys = ingest
    r = client.post("/v1/events", json={
        "surface": "desktop",
        "events": [{"name": "client_run_ms", "value": 4200, "outcome": "ok",
                    "props": {"run_id": "r-1", "agent_id": "main"}}],
    })
    assert r.status_code == 200 and r.json() == {"ok": True, "accepted": 1, "dropped": 0}

    records = _emitted(capsys)
    assert "client_run_ms" in _names(records)
    published = next(r for r in records if r.get("client_run_ms") is not None)
    assert published["client_run_ms"] == 4200
    assert published["run_id"] == "r-1", "correlation ids are the point of the payload"


def test_an_unknown_metric_name_is_dropped_not_published(ingest):
    """A metric name is a billed resource. A client that can name one can bill us forever."""
    client, capsys = ingest
    r = client.post("/v1/events", json={"events": [
        {"name": "client_run_ms", "value": 1},
        {"name": "please_bill_me_forever", "value": 1},
        {"name": "AWS/Billing", "value": 999},
    ]})
    assert r.json() == {"ok": True, "accepted": 1, "dropped": 2}

    names = _names(_emitted(capsys))
    assert "client_run_ms" in names
    assert "please_bill_me_forever" not in names and "AWS/Billing" not in names
    # Dropping silently would let a client drift out of schema unnoticed on BOTH sides.
    assert "ingest_rejected_total" in names


def test_a_dimension_value_outside_the_vocabulary_collapses_to_other(ingest):
    """Same bill, worse: dimensions multiply. Unknown values become one bucket, not one metric."""
    client, capsys = ingest
    client.post("/v1/events", json={"events": [
        {"name": "client_run_total", "value": 1, "outcome": "d3adb33f-unique-per-request"},
    ]})
    published = next(r for r in _emitted(capsys) if r.get("client_run_total") is not None)
    assert published["outcome"] == "other"


def test_properties_not_on_the_allowlist_never_reach_the_line(ingest):
    """The client scrubs too — on the user's machine, in code they can replace."""
    client, capsys = ingest
    client.post("/v1/events", json={"events": [{
        "name": "client_run_ms", "value": 10,
        "props": {
            "run_id": "r-2",                       # allowed
            "prompt": "my private message text",   # not allowed
            "file_path": "C:/Users/me/secrets.txt",
            "api_key": "sk-livekey",
        },
    }]})
    published = next(r for r in _emitted(capsys) if r.get("client_run_ms") is not None)
    assert published["run_id"] == "r-2"
    body = json.dumps(published)
    assert "private message" not in body
    assert "secrets.txt" not in body
    assert "sk-livekey" not in body


def test_a_client_cannot_attribute_its_events_to_another_account(ingest):
    """Unauthenticated, so there is no account to claim. A body-supplied one must be discarded —
    otherwise every per-account anomaly signal is worthless."""
    client, capsys = ingest
    client.post("/v1/events", json={"events": [
        {"name": "client_run_ms", "value": 10, "props": {"account_id": "someone-elses-account"}},
    ]})
    published = next(r for r in _emitted(capsys) if r.get("client_run_ms") is not None)
    assert "account_id" not in published


def test_values_are_clamped_rather_than_trusted_or_rejected(ingest):
    """Clamped, not dropped: an implausible duration is still evidence something hung, and losing
    it hides the incident. Unclamped it poisons every percentile it lands in."""
    client, capsys = ingest
    client.post("/v1/events", json={"events": [
        {"name": "client_run_ms", "value": 10 ** 12},
        {"name": "client_model_ms", "value": -5},
    ]})
    # `_emitted` DRAINS capsys, so read once and index what came back.
    values = {name: record[name]
              for record in _emitted(capsys)
              for name in ("client_run_ms", "client_model_ms")
              if record.get(name) is not None}
    assert values["client_run_ms"] == 3_600_000.0, "an hour is the ceiling for a run"
    assert values["client_model_ms"] == 0.0, "negative durations become zero, not negative"


def test_nan_and_infinity_are_refused(ingest):
    """json.loads accepts NaN/Infinity by default, and either one silently destroys a statistic."""
    client, _ = ingest
    r = client.post("/v1/events", content='{"events":[{"name":"client_run_ms","value":NaN}]}',
                    headers={"Content-Type": "application/json"})
    assert r.json()["accepted"] == 0


def test_one_bad_event_does_not_reject_the_batch(ingest):
    """A 4xx leaves the client no sane option but to drop its whole buffer, losing the good
    events with the bad one."""
    client, _ = ingest
    r = client.post("/v1/events", json={"events": [
        {"name": "client_run_ms", "value": 1},
        "not even an object",
        {"name": "client_turns", "value": 3},
    ]})
    assert r.status_code == 200 and r.json()["accepted"] == 2


def test_an_oversized_batch_is_refused_outright(ingest):
    client, _ = ingest
    r = client.post("/v1/events", json={
        "events": [{"name": "client_run_ms", "value": 1} for _ in range(101)]
    })
    assert r.status_code == 413


def test_the_rate_limit_stops_a_machine_in_a_reboot_loop(ingest, monkeypatch):
    client, _ = ingest
    monkeypatch.setenv("INGEST_RATE_LIMIT", "3/60")
    body = {"events": [{"name": "client_daemon_start_total", "value": 1}]}
    codes = [client.post("/v1/events", json=body).status_code for _ in range(5)]
    assert codes.count(200) == 3 and codes.count(429) == 2


def test_client_side_buffer_loss_is_recorded_as_loss(ingest):
    """A laptop offline for a day must show up as dropped events, not as a quiet gap that reads
    like 'nothing happened'."""
    client, capsys = ingest
    client.post("/v1/events", json={
        "surface": "desktop", "dropped": 137,
        "events": [{"name": "client_run_ms", "value": 1}],
    })
    published = next(r for r in _emitted(capsys)
                     if r.get("client_buffer_dropped_total") is not None)
    assert published["client_buffer_dropped_total"] == 137


def test_the_schema_endpoint_matches_what_is_enforced(ingest):
    """A client can check itself against a running deployment instead of discovering a mismatch
    as silently-dropped events weeks later — so the two must not be able to drift."""
    client, _ = ingest
    schema = client.get("/v1/schema").json()
    assert set(schema["metrics"]) == set(client.app_module.ALLOWED_METRICS)  # type: ignore[attr-defined]
    assert set(schema["dimensions"]) == set(client.app_module.ALLOWED_DIMENSIONS)  # type: ignore[attr-defined]
    assert schema["max_events_per_batch"] == client.app_module.MAX_EVENTS_PER_BATCH  # type: ignore[attr-defined]


def test_health_and_readiness_do_not_depend_on_accounts(ingest):
    """Accounts being unreachable degrades events to anonymous; it must not make ingest unready,
    or an accounts outage would take the telemetry that reports it down too."""
    client, _ = ingest
    assert client.get("/health").json()["ok"] is True
    assert client.get("/health/ready").json()["ok"] is True

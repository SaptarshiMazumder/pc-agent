"""The mail path, end to end: desktop uploader -> ingest -> EMF (plan items 5.1 + 5.2).

The unit tests cover each half against a stub. This one runs them against EACH OTHER, because
every interesting bug in this feature lives in the gap between them: a name the client forwards
that the server does not allow, a field the client sends that the server strips, an envelope shape
one side changed. Both halves pass their own tests in every one of those cases, and no telemetry
arrives — which looks exactly like "the user has not opted in".

The transport is FastAPI's TestClient rather than a socket: it drives the same ASGI app uvicorn
would, so everything above the TCP layer is real, and the test needs no port.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

V2 = Path(__file__).resolve().parents[2]


@pytest.fixture
def wired(monkeypatch, capsys):
    """An Uploader whose HTTP goes straight into a live ingest app."""
    monkeypatch.setenv("AGENTD_SERVICE", "ingest")
    monkeypatch.setenv("AGENTD_TELEMETRY", "1")
    monkeypatch.setenv("INGEST_RATE_LIMIT", "0/0")
    monkeypatch.delenv("ACCOUNTS_URL", raising=False)
    monkeypatch.delenv("AGENTD_TELEMETRY_FORWARD", raising=False)

    spec = importlib.util.spec_from_file_location("ingest_app_e2e", V2 / "ingest" / "app.py")
    assert spec and spec.loader
    ingest = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ingest)

    from agentd_telemetry import uploader as up

    client = TestClient(ingest.app)

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return None

    def urlopen(request, timeout=None):
        headers = {k: v for k, v in request.header_items()}
        reply = client.post(request.full_url, content=request.data, headers=headers)
        reply.raise_for_status()
        return _Response()

    monkeypatch.setattr(up.urllib.request, "urlopen", urlopen)

    sender = up.Uploader()
    sender.configure(enabled=True, url="http://ingest.test")
    with client:
        yield sender, capsys


def _published(capsys) -> dict[str, dict]:
    """metric name -> the EMF record ingest printed."""
    out = {}
    for line in capsys.readouterr().out.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and "_aws" in record:
            name = record["_aws"]["CloudWatchMetrics"][0]["Metrics"][0]["Name"]
            out[name] = record
    return out


def _metric(name: str, value: float, **fields) -> dict:
    return {
        "_aws": {"Timestamp": 0, "CloudWatchMetrics": [
            {"Namespace": "agentd", "Dimensions": [["service"]],
             "Metrics": [{"Name": name, "Unit": "Count"}]}]},
        name: value, "service": "daemon", **fields,
    }


def test_a_desktop_run_becomes_a_cloudwatch_metric(wired):
    """The whole point of Phase 5: a number measured on the user's PC, where our stdout reaches
    nobody, ends up as a metric in our namespace."""
    sender, capsys = wired
    sender.offer(_metric("run_duration_ms", 6687, outcome="ok", run_id="e2e-1", agent_id="main"))
    sender.offer(_metric("daemon_start_total", 1, outcome="ok"))
    assert sender.flush() == 2

    published = _published(capsys)
    assert published["client_run_ms"]["client_run_ms"] == 6687
    assert published["client_run_ms"]["outcome"] == "ok"
    assert published["client_run_ms"]["run_id"] == "e2e-1"
    assert published["client_daemon_start_total"]["client_daemon_start_total"] == 1


def test_every_name_the_client_forwards_is_one_the_server_accepts(wired):
    """The drift test. Both halves keep their own list; if they ever disagree, telemetry silently
    stops arriving and looks identical to nobody having opted in."""
    sender, capsys = wired
    from agentd_telemetry import uploader as up

    for local_name, remote_name in up._DEFAULT_FORWARD.items():
        sender.offer(_metric(local_name, 1, outcome="ok"))
    assert sender.flush() == len(up._DEFAULT_FORWARD)

    published = set(_published(capsys))
    missing = set(up._DEFAULT_FORWARD.values()) - published
    assert not missing, f"the client forwards names ingest drops on the floor: {sorted(missing)}"


def test_content_the_client_never_should_have_sent_still_does_not_land(wired):
    """Two independent gates. This proves the SECOND one works even if the first is bypassed —
    which matters because the first runs on a machine the user controls."""
    sender, capsys = wired
    sender.offer(_metric("run_duration_ms", 10, run_id="e2e-2",
                         prompt="a private message", file_path="C:/secrets.txt"))
    sender.flush()
    body = json.dumps(_published(capsys))
    assert "private message" not in body and "secrets.txt" not in body
    assert "e2e-2" in body, "correlation ids must still make it through"


def test_client_side_buffer_loss_survives_the_round_trip(wired, monkeypatch):
    """An offline laptop shows up as loss on our graphs rather than as a silent gap."""
    monkeypatch.setenv("AGENTD_TELEMETRY_BUFFER", "3")
    from agentd_telemetry import uploader as up

    sender = up.Uploader()
    sender.configure(enabled=True, url="http://ingest.test")
    for i in range(10):
        sender.offer(_metric("run_duration_ms", i))
    sender.flush()

    _s, capsys = wired
    published = _published(capsys)
    assert published["client_buffer_dropped_total"]["client_buffer_dropped_total"] == 7

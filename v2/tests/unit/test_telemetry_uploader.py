"""Contract tests for the desktop diagnostics uploader (plan item 5.1).

The uploader runs on a machine we do not own and sends data off it, so the properties that matter
are the ones about restraint:

  * OFF unless explicitly enabled AND pointed somewhere
  * only a named shortlist of metrics ever leaves — not everything `emit` sees
  * the buffer is bounded, and what it discarded is REPORTED rather than hidden
  * turning it off stops it now, and drops what was already queued
  * it never raises into the caller, whatever the network does
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

V2 = Path(__file__).resolve().parents[2]


@pytest.fixture
def up(monkeypatch):
    """A fresh Uploader per test — the module-level one is process-wide state."""
    monkeypatch.delenv("AGENTD_TELEMETRY_FORWARD", raising=False)
    monkeypatch.delenv("AGENTD_TELEMETRY_UPLOAD", raising=False)
    monkeypatch.delenv("AGENTD_TELEMETRY_UPLOAD_URL", raising=False)
    from agentd_telemetry import uploader as module

    return module


def _record(name: str, value: float = 1, **fields) -> dict:
    """The shape emf.metric_record produces, which is what `offer` is handed."""
    return {
        "_aws": {"Timestamp": 0, "CloudWatchMetrics": [
            {"Namespace": "agentd", "Dimensions": [["service"]],
             "Metrics": [{"Name": name, "Unit": "Count"}]}
        ]},
        name: value,
        "service": "daemon",
        **fields,
    }


def test_it_is_inert_until_enabled_and_pointed_somewhere(up):
    u = up.Uploader()
    assert u.active is False
    u.offer(_record("run_duration_ms", 100))
    assert u.status()["queued"] == 0, "nothing may be collected before consent"

    u.configure(enabled=True)  # enabled, but no URL
    assert u.active is False
    u.offer(_record("run_duration_ms", 100))
    assert u.status()["queued"] == 0, "a build with nowhere to send must not collect either"


def test_only_the_named_shortlist_is_forwarded(up):
    u = up.Uploader()
    u.configure(enabled=True, url="http://ingest.test")
    u.offer(_record("run_duration_ms", 4200, outcome="ok", run_id="r-1"))
    u.offer(_record("tool_call_total", 1))          # emitted constantly, not forwarded
    u.offer(_record("skills_prompt_chars", 900))    # ditto
    assert u.status()["queued"] == 1


def test_names_are_translated_on_the_way_out(up):
    """`run_duration_ms` measured on a user's laptop and the same name measured in our own cloud
    daemon are different populations; merging them would make both graphs meaningless."""
    u = up.Uploader()
    u.configure(enabled=True, url="http://ingest.test")
    u.offer(_record("run_duration_ms", 4200, outcome="ok"))

    sent = {}
    u._queue and sent.update(u._queue[0])
    assert sent["name"] == "client_run_ms"
    assert sent["value"] == 4200 and sent["outcome"] == "ok"


def test_only_correlation_fields_ride_along(up):
    u = up.Uploader()
    u.configure(enabled=True, url="http://ingest.test")
    u.offer(_record("run_duration_ms", 1, run_id="r-9", agent_id="main",
                    account_id="acct-1", model="gemini/x"))
    props = u._queue[0]["props"]
    assert props == {"run_id": "r-9", "agent_id": "main"}
    # account_id is deliberately NOT sent from here: the receiver takes it from the token, so a
    # client-supplied one could only ever be a claim about someone else.
    assert "account_id" not in props and "model" not in props


def test_the_buffer_is_bounded_and_reports_what_it_dropped(up, monkeypatch):
    monkeypatch.setenv("AGENTD_TELEMETRY_BUFFER", "5")
    u = up.Uploader()
    u.configure(enabled=True, url="http://ingest.test")
    for i in range(12):
        u.offer(_record("run_duration_ms", i))
    status = u.status()
    assert status["queued"] == 5, "an offline laptop must not grow the process"
    assert status["dropped"] == 7, "silent loss reads as 'nothing happened' on the graph"
    # And the OLDEST went: recent events are the ones that explain the current problem.
    assert u._queue[-1]["value"] == 11


def test_turning_it_off_discards_what_was_queued(up):
    """Re-enabling later must not ship events recorded while the user had said no."""
    u = up.Uploader()
    u.configure(enabled=True, url="http://ingest.test")
    u.offer(_record("run_duration_ms", 1))
    assert u.status()["queued"] == 1

    u.configure(enabled=False)
    assert u.status()["queued"] == 0 and u.active is False


def test_flush_posts_a_batch_with_the_session_token(up, monkeypatch):
    u = up.Uploader()
    u.configure(enabled=True, url="http://ingest.test", token="tok-123")
    u.offer(_record("run_duration_ms", 7))
    u.offer(_record("run_total", 1, outcome="ok"))

    seen = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return None

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        seen["auth"] = request.get_header("Authorization")
        seen["body"] = json.loads(request.data)
        return _Resp()

    monkeypatch.setattr(up.urllib.request, "urlopen", fake_urlopen)
    assert u.flush() == 2

    assert seen["url"] == "http://ingest.test/v1/events"
    assert seen["auth"] == "Bearer tok-123"
    assert seen["body"]["surface"] == "desktop"
    assert [e["name"] for e in seen["body"]["events"]] == ["client_run_ms", "client_run_total"]
    assert u.status()["queued"] == 0


def test_a_dead_endpoint_never_raises_and_never_retries(up, monkeypatch):
    """A retry loop against a broken ingest turns an outage into a client-side memory leak plus a
    thundering herd on recovery. These are metrics, not billing rows."""
    import urllib.error

    u = up.Uploader()
    u.configure(enabled=True, url="http://ingest.test")
    u.offer(_record("run_duration_ms", 1))

    def boom(*_a, **_k):
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(up.urllib.request, "urlopen", boom)
    assert u.flush() == 0
    assert u.status()["queued"] == 0, "the batch is discarded, not re-queued forever"


def test_offer_never_raises_on_a_malformed_record(up):
    """`emit` calls this for every metric in the process. It may not be able to break the caller."""
    u = up.Uploader()
    u.configure(enabled=True, url="http://ingest.test")
    for junk in ({}, {"_aws": {}}, {"_aws": {"CloudWatchMetrics": []}}, {"_aws": None}):
        u.offer(junk)  # must not raise
    assert u.status()["queued"] == 0


def test_the_forward_list_is_configurable_without_a_release(up, monkeypatch):
    monkeypatch.setenv("AGENTD_TELEMETRY_FORWARD", "run_total=client_run_total")
    u = up.Uploader()
    u.configure(enabled=True, url="http://ingest.test")
    u.offer(_record("run_duration_ms", 1))   # no longer on the list
    u.offer(_record("run_total", 1))
    assert [e["name"] for e in u._queue] == ["client_run_total"]

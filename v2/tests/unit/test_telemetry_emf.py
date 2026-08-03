"""EMF record shape — specifically the dimension sets every CloudWatch alarm binds to.

These are not tests of a formatting detail. `infra/modules/alarms.tf` declares plain metric
alarms like `dimensions = { service = "model-proxy", outcome = "fail" }`, and CloudWatch
creates ONE METRIC PER EXACT DIMENSION SET. If a rollup here stops being emitted, the alarms
do not error — they install cleanly, match nothing, and sit in INSUFFICIENT_DATA forever while
appearing healthy in the console. That failure is invisible from both sides, so it is pinned
here, at the only place the two files agree on anything.

The module reads its config at import, so tests that change the environment reimport it.
"""

from __future__ import annotations

import importlib
import json

import pytest

from agentd_telemetry import emf


@pytest.fixture(autouse=True)
def _restore_module():
    """Undo any env-driven reimport, so one test cannot leak config into the next."""
    yield
    importlib.reload(emf)


def _sets(record: dict) -> list[list[str]]:
    return record["_aws"]["CloudWatchMetrics"][0]["Dimensions"]


def _record(dimensions: dict, name: str = "ledger_write_total", value: float = 1) -> dict:
    return emf.metric_record(name, value, "Count", dimensions, {})


# --- the rollups alarms depend on --------------------------------------------


def test_extra_dimensions_do_not_hide_the_rollups():
    """The regression that made SEARCH() look necessary: `reason` is present on failures
    and absent on successes, so the exact set is useless to an alarm."""
    fail = _sets(_record({"outcome": "fail", "reason": "http_error"}))
    ok = _sets(_record({"outcome": "ok"}))

    # Both publish the set the ledger-write alarm names, despite differing exact sets.
    assert ["outcome", "service"] in fail
    assert ["outcome", "service"] in ok
    # ...and the detailed breakdown is still published for dashboards and Logs Insights.
    assert ["outcome", "reason", "service"] in fail


def test_coarsest_rollup_is_always_present():
    """`{service}` is what the p99-across-all-outcomes and cost alarms bind to."""
    for dimensions in ({}, {"outcome": "ok"}, {"outcome": "ok", "cache": "hit", "credential": "session"}):
        assert ["service"] in _sets(_record(dimensions))


def test_no_duplicate_dimension_sets():
    """A repeated set publishes -- and BILLS FOR -- the same metric twice."""
    for dimensions in ({}, {"outcome": "ok"}, {"outcome": "fail", "reason": "x"}):
        sets = _sets(_record(dimensions))
        assert len(sets) == len({tuple(s) for s in sets}), sets


def test_a_dimensionless_metric_publishes_exactly_one_set():
    """unbilled_cost_usd / model_cost_usd carry no outcome; they must not gain a phantom set."""
    assert _sets(_record({}, name="unbilled_cost_usd")) == [["service"]]


def test_rollup_is_skipped_when_its_keys_are_absent():
    """A metric with no `outcome` gets {service} only, never a set naming a missing key."""
    sets = _sets(_record({"credential": "master"}, name="auth_total"))
    assert ["service"] in sets
    assert all("outcome" not in s for s in sets)


# --- the record around them ---------------------------------------------------


def test_dimension_values_appear_as_fields_and_the_record_is_serialisable():
    """EMF requires every declared dimension to exist as a top-level field on the line."""
    record = _record({"outcome": "fail", "reason": "http_error"})
    for dimension_set in _sets(record):
        for key in dimension_set:
            assert key in record, key
    json.loads(json.dumps(record, default=str))  # emit() would silently drop it otherwise


def test_service_defaults_into_every_dimension_set():
    record = _record({"outcome": "ok"})
    assert record["service"] == emf.service()
    assert all("service" in s for s in _sets(record))


def test_rollup_keys_are_configurable(monkeypatch):
    """Which keys are alarm-worthy is a deployment decision (see AGENTD_TELEMETRY_ROLLUP_KEYS)."""
    monkeypatch.setenv("AGENTD_TELEMETRY_ROLLUP_KEYS", "service")
    reloaded = importlib.reload(emf)
    # `reason` keeps the EXACT set distinct from the rollups, so this measures the config
    # and not the exact set (which is emitted either way).
    record = reloaded.metric_record("ledger_write_total", 1, "Count", {"outcome": "fail", "reason": "x"}, {})
    sets = record["_aws"]["CloudWatchMetrics"][0]["Dimensions"]
    assert ["service"] in sets
    assert ["outcome", "reason", "service"] in sets
    assert ["outcome", "service"] not in sets  # no longer requested, so no longer billed

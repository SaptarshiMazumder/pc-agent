import urllib.error
from unittest.mock import Mock

import pytest

from vast.infrastructure.http_instance_probe import HttpInstanceProbe


IDLE_QUEUE = {"queue_running": [], "queue_pending": []}
IDLE_MANAGER = {"is_processing": False, "total_count": 1, "done_count": 1, "in_progress_count": 0}


def probe(queue=IDLE_QUEUE, manager=IDLE_MANAGER, activity=None):
    instance = HttpInstanceProbe(now=lambda: 1000)
    instance._get_json = Mock(side_effect=[queue, manager, activity])
    return instance.busy("http://gpu")


def test_idle_requires_all_sources_to_be_known():
    assert probe(activity={"jobs": {}}) is False
    assert probe(activity=HttpInstanceProbe._ABSENT) is False
    assert probe(manager=HttpInstanceProbe._ABSENT, activity=HttpInstanceProbe._ABSENT) is False


@pytest.mark.parametrize("queue", [None, {}, {"queue_running": "oops", "queue_pending": []}])
def test_failed_or_malformed_queue_is_unknown(queue):
    assert probe(queue=queue, activity={"jobs": {}}) is None


@pytest.mark.parametrize("manager", [None, {}, {"is_processing": False, "total_count": "oops"}])
def test_manager_outage_is_not_idle(manager):
    assert probe(manager=manager, activity={"jobs": {}}) is None


@pytest.mark.parametrize("activity", [None, {}, {"jobs": {"a": {"state": "downloading", "updated_at": 100}}},
                                      {"jobs": {"a": "broken"}}])
def test_missing_malformed_or_stale_download_status_is_unknown(activity):
    assert probe(activity=activity) is None


def test_direct_download_is_busy_without_manager_or_client_heartbeat():
    assert probe(activity={"jobs": {"a": {"state": "downloading", "updated_at": 990}}}) is True


def test_any_positive_busy_signal_wins_over_another_outage():
    assert probe(queue=None, manager={"is_processing": True}) is True
    assert probe(queue={"queue_running": ["render"], "queue_pending": []}) is True
    assert probe(queue=None, manager=None, activity={"jobs": {"a": {"state": "starting", "updated_at": 999}}}) is True


@pytest.mark.parametrize("code", [401, 403, 500, 503])
def test_http_errors_are_unknown_not_optional_absence(monkeypatch, code):
    monkeypatch.setattr("urllib.request.urlopen", Mock(side_effect=urllib.error.HTTPError("http://gpu", code, "error", {}, None)))
    assert HttpInstanceProbe()._get_json("http://gpu", "/test", "secret") is None


def test_only_actual_404_means_optional_feature_absent(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", Mock(side_effect=urllib.error.HTTPError("http://gpu", 404, "missing", {}, None)))
    assert HttpInstanceProbe()._get_json("http://gpu", "/test", "secret") is HttpInstanceProbe._ABSENT

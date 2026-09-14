from contextlib import nullcontext
from dataclasses import replace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vast.application.instance_settings import InstanceSettings
from vast.application.services.instance_service import InstanceService
from vast.domain.errors import VastError
from vast.domain.instance import InstanceRow, MachineInstance
from vast.infrastructure.vast_marketplace import VastMarketplace
from vast.presentation.vast_router import build_vast_router


def row():
    return InstanceRow(id="row1", account_id="alice", instance_id=123, machine_id=12,
                       url="http://gpu:8000", state="running", hourly_usd=.2, created_at=0,
                       last_seen_at=0, lease_until=0, dead_at=None, dead_reason="", auth_token="test-only")


def machine():
    return MachineInstance(instance_id=123, label="agentd-row1", status="running", machine_id=12,
                           hourly_usd=.2, url="http://gpu:8000", portal_url="http://gpu:9000")


def service(record, live):
    store = Mock()
    store.live_for.return_value = record
    market = Mock()
    market.get_instance.return_value = live
    instance = InstanceService(db=nullcontext, store=store, marketplace=lambda: market,
                               settings=InstanceSettings(), now=lambda: 0, probe=Mock())
    return instance, store, market


def test_portal_port_is_resolved_independently_of_comfy_port():
    live = VastMarketplace("test")._shape({"id": 123, "public_ipaddr": "203.0.113.4",
        "ports": {"22/tcp": [{"HostPort": "7000"}], "1111/tcp": [{"HostPort": "9000"}],
                  "8188/tcp": [{"HostPort": "8000"}]}})
    assert live.url == "http://203.0.113.4:8000"
    assert live.portal_url == "http://203.0.113.4:9000"


def test_existing_owned_rental_exposes_connection_without_renting():
    instance, store, market = service(row(), machine())
    result = instance.download_connection("alice")
    assert result["portal_url"] == "http://gpu:9000"
    assert result["auth"] == "Bearer test-only"
    store.live_for.assert_called_once_with(None, "alice")
    market.get_instance.assert_called_once_with(123)
    market.create.assert_not_called()


@pytest.mark.parametrize("live", [None, replace(machine(), label="somebody-else"),
                                   replace(machine(), instance_id=456), replace(machine(), portal_url=None)])
def test_wrong_or_missing_rental_never_exposes_credentials(live):
    instance, _, _ = service(row(), live)
    with pytest.raises(VastError):
        instance.download_connection("alice")


def test_no_rental_does_not_start_one():
    instance, _, market = service(None, None)
    with pytest.raises(VastError):
        instance.download_connection("alice")
    market.get_instance.assert_not_called()
    market.create.assert_not_called()


def test_route_enforces_account_isolation():
    instance = Mock(configured=True)
    instance.download_connection.return_value = {"portal_url": "http://gpu:9000"}
    app = FastAPI()
    app.include_router(build_vast_router(service=instance, reaper=Mock(),
                                        require_internal=lambda _: False, resolve_bearer=lambda token: "alice"))
    with TestClient(app) as client:
        denied = client.post("/vast/download-connection", json={"account_id": "bob"},
                             headers={"Authorization": "Bearer alice-token"})
        assert denied.status_code == 403
        instance.download_connection.assert_not_called()
        accepted = client.post("/vast/download-connection", json={"account_id": "alice"},
                               headers={"Authorization": "Bearer alice-token"})
        assert accepted.status_code == 200
        instance.download_connection.assert_called_once_with("alice")

"""Connecting a Vast machine is "your API key, pick your machine" — no link, no token hunting."""

from __future__ import annotations

import json

import pytest

from agent_runtime.infrastructure.net.outbound import Response
from vast_account_client import VastAccountClient
from vast_machine_connector import VastMachineConnector

ROW = {
    "id": 52928784, "label": "", "actual_status": "running", "gpu_name": "RTX 5060 Ti", "num_gpus": 1,
    "public_ipaddr": "158.181.52.18",
    "ports": {"22/tcp": [{"HostPort": "44145"}], "1111/tcp": [{"HostPort": "44001"}],
              "8188/tcp": [{"HostPort": "44344"}]},
    "jupyter_token": "jt", "extra_env": [["PORTAL_CONFIG", "x"]],
}


def _fetch(routes):
    calls = []

    def fetch(url, **kw):
        calls.append((url, kw))
        for part, response in routes.items():
            if part in url:
                return response(url, kw) if callable(response) else response
        return Response(error="unreachable", url=url)

    return fetch, calls


def test_the_machines_are_listed_with_the_person_s_key_as_a_name():
    fetch, calls = _fetch({"/instances/": Response(status=200, text=json.dumps({"instances": [ROW]}))})
    machines = VastAccountClient(fetch).machines()
    assert machines == [{"id": 52928784, "name": "Vast machine 52928784", "gpu": "RTX 5060 Ti",
                         "status": "running", "running": True}]
    assert calls[0][1]["headers"]["Authorization"] == "Bearer ${USER_VAST_API_KEY}"


def test_a_refused_key_says_where_to_fix_it():
    fetch, _ = _fetch({"/instances/": Response(status=401, text="")})
    with pytest.raises(ValueError, match="API key"):
        VastAccountClient(fetch).machines()


def test_access_finds_the_portal_port_and_the_tokens():
    fetch, _ = _fetch({"/instances/52928784/": Response(status=200, text=json.dumps({"instances": ROW}))})
    access = VastAccountClient(fetch).access(52928784)
    assert access["portal_origins"] == ["http://158.181.52.18:44001", "https://158.181.52.18:44001"]
    assert access["tokens"] == ["jt"]


def test_a_stopped_machine_is_refused_plainly():
    fetch, _ = _fetch({"/instances/1/": Response(status=200, text=json.dumps({"instances": {**ROW, "actual_status": "exited"}}))})
    with pytest.raises(ValueError, match="not running"):
        VastAccountClient(fetch).access(1)


def test_the_connector_finds_the_way_in_that_answers():
    services = [{"name": "ComfyUI", "direct_url": "http://158.181.52.18:44344/"}]

    def portal(url, kw):
        # https is not served; the token jt opens the http portal
        if url.startswith("https://"):
            return Response(error="ssl", url=url)
        ok = kw.get("headers", {}).get("Authorization") == "Bearer jt"
        return Response(status=200 if ok else 401, text=json.dumps(services) if ok else "")

    fetch, _ = _fetch({
        "/instances/52928784/": Response(status=200, text=json.dumps({"instances": ROW})),
        "/capabilities/services": portal,
        "/api/system_stats": Response(status=200, text='{"system": {}}'),
    })
    from comfy_connection_probe import ComfyConnectionProbe

    record = VastMachineConnector(VastAccountClient(fetch), ComfyConnectionProbe(fetch)).connect(52928784)
    assert record["kind"] == "user_vast"
    assert record["portal_url"] == "http://158.181.52.18:44001"
    assert record["url"] == "http://158.181.52.18:44344"
    assert record["vast_machine_id"] == 52928784 and "jt" not in record["label"]

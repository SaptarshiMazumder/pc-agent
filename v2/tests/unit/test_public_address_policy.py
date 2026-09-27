"""A hosted daemon dials only the public internet for a URL a person gave — never its own insides."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_runtime.infrastructure.net.public_address_policy import (
    PublicAddressPolicy,
    PublicAddressRefused,
)
from agent_runtime.infrastructure.tools.sandbox.fetch_broker import SandboxFetchBroker


def _resolving_to(ip):
    return lambda host, port: [(None, None, None, None, (ip, 0))]


@pytest.mark.parametrize("host", ["169.254.169.254", "127.0.0.1", "10.0.0.5", "192.168.1.2", "::1"])
def test_internal_addresses_are_refused(host):
    with pytest.raises(PublicAddressRefused):
        PublicAddressPolicy().check(host)


def test_a_name_that_resolves_inside_is_refused():
    with pytest.raises(PublicAddressRefused):
        PublicAddressPolicy(resolve=_resolving_to("172.17.0.1")).check("accounts.internal")


def test_a_public_address_passes():
    PublicAddressPolicy(resolve=_resolving_to("180.189.55.43")).check("my-vast-box.example")


def _broker(hosted):
    return SandboxFetchBroker(
        SimpleNamespace(hosted=hosted), plugin_id="p", tool_name="t",
        grant=SimpleNamespace(timeout_s=0, net_allowlist=(), fs_paths=(), read_paths=()),
    )


def test_hosted_guards_a_typed_url_but_not_the_platform():
    broker = _broker(hosted=True)
    assert broker._public_only("http://10.0.0.5:8188/api/prompt") is True
    assert broker._public_only("${AGENTD_ACCOUNTS_URL}/vast/heartbeat") is False


def test_desktop_reaches_localhost():
    assert _broker(hosted=False)._public_only("http://127.0.0.1:8188/api/prompt") is False

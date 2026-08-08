"""build_gateway is the daemon's ENTIRE boot path (main.py calls it and nothing else).

It had no test, so a NameError in it — `_late` referenced outside the function that defines
it — shipped green through the whole suite and killed every boot. These tests exercise the
real composition root end to end, and pin the late-binding contract that bug came from.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_runtime.config import load_config
from agent_runtime.main.container import build_gateway, build_service


def test_build_gateway_boots():
    """The daemon's boot path runs to completion."""
    gateway = build_gateway(load_config())
    assert gateway is not None
    assert gateway.service is not None
    assert gateway.registry is not None


def test_gateway_is_late_bound_for_plugins():
    """Plugins are discovered BEFORE the Gateway exists, so they get a thunk that resolves
    later. If the Gateway never lands in the shared late dict, agent-builder's reload_agent
    silently stops refreshing clients — a no-op with no error anywhere."""
    late: dict = {}
    build_service(load_config(), None, None, late=late)
    assert late.get("service") is not None, "build_service must publish the service"

    gateway = build_gateway(load_config())
    assert gateway.broadcast_agents_changed is not None
    # no running loop here: the broadcast must degrade quietly, not raise into the tool
    gateway.broadcast_agents_changed()


def test_each_build_owns_its_late_dict():
    """Two containers in one process (the suite does this) must not share late state."""
    first: dict = {}
    second: dict = {}
    build_service(load_config(), None, None, late=first)
    build_service(load_config(), None, None, late=second)
    assert first["service"] is not second["service"]

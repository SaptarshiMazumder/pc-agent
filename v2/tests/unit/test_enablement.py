"""apply_enablement -- the GLOBAL (catalog-wide) tool on/off filter. Sibling of select_tools
(per-agent), but applied once to the whole catalog, uniformly across every source."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_runtime.domain.agent import apply_enablement


class T:
    def __init__(self, name):
        self.name = name


def names(tools):
    return [t.name for t in tools]


def test_disabled_removes_by_name():
    tools = [T("read"), T("browser"), T("computer")]
    assert names(apply_enablement(tools, disabled=["computer"])) == ["read", "browser"]


def test_disabled_trailing_glob():
    tools = [T("web_search"), T("web_fetch"), T("read")]
    assert names(apply_enablement(tools, disabled=["web_*"])) == ["read"]


def test_enabled_is_a_strict_allowlist():
    tools = [T("read"), T("write"), T("browser")]
    assert names(apply_enablement(tools, enabled=["read", "browser"])) == ["read", "browser"]


def test_empty_keeps_everything():
    tools = [T("read"), T("write")]
    assert names(apply_enablement(tools)) == ["read", "write"]
    assert names(apply_enablement(tools, enabled=[], disabled=[])) == ["read", "write"]


def test_disabled_wins_over_enabled():
    tools = [T("read"), T("write")]
    assert names(apply_enablement(tools, enabled=["read", "write"], disabled=["write"])) == ["read"]


def test_applies_uniformly_to_mcp_named_tools():
    # an MCP tool's namespaced name is just a name -> same filter, no special-casing
    tools = [T("read"), T("google__gmail_send"), T("google__drive_export")]
    assert names(apply_enablement(tools, disabled=["google__*"])) == ["read"]

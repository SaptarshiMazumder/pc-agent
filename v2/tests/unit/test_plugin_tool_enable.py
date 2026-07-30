import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_runtime.domain.agent import apply_plugin_enablement


def _t(name, plugin):
    # a tool carries its provenance as _plugin_id (set by discovery) + a .name
    return SimpleNamespace(name=name, _plugin_id=plugin)


TOOLS = [_t("web_search", "web"), _t("web_fetch", "web"), _t("read", "core_fs")]


def test_no_config_keeps_everything():
    assert [t.name for t in apply_plugin_enablement(TOOLS, {})] == [
        "web_search",
        "web_fetch",
        "read",
    ]
    assert len(apply_plugin_enablement(TOOLS, None)) == 3


def test_disable_single_tool():
    plugins = {"web": {"tools": {"web_fetch": {"enabled": False}}}}
    assert [t.name for t in apply_plugin_enablement(TOOLS, plugins)] == ["web_search", "read"]


def test_enabled_true_or_omitted_is_kept():
    plugins = {"web": {"tools": {"web_search": {"enabled": True}, "web_fetch": {"model": "x"}}}}
    assert [t.name for t in apply_plugin_enablement(TOOLS, plugins)] == [
        "web_search",
        "web_fetch",
        "read",
    ]


def test_only_matching_plugin_tool_is_dropped():
    # a same-named tool in a different plugin is unaffected
    plugins = {
        "core_fs": {"tools": {"web_fetch": {"enabled": False}}}
    }  # wrong plugin for web_fetch
    assert len(apply_plugin_enablement(TOOLS, plugins)) == 3

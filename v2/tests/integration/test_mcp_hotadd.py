"""MCP hot-add — /mcp add connects a server live + persists to config.mcp_servers (no restart);
/mcp list unifies config + plugin-MCP servers; /mcp remove drops it. The central registry."""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_runtime.config import Config, McpServerConfig
from agent_runtime.presentation import gateway as gw_mod
from agent_runtime.presentation.gateway import Gateway, _persist_mcp_servers, _server_dict


class _Tool:
    def __init__(self, name):
        self.name, self.description, self.label, self.concurrency = name, "", "", "parallel"


class FakeProvider:
    async def add_server(self, cfg):
        return [_Tool(f"{cfg.name}__hello"), _Tool(f"{cfg.name}__bye")]


class FakeService:
    def __init__(self, tools=None):
        self._tools = list(tools or [])
        self.added = []

    def add_tools(self, t):
        self.added.extend(t)
        self._tools.extend(t)

    def list_tools(self, agent_id=None):
        return [{"name": getattr(t, "name", "")} for t in self._tools]

    def remove_tools(self, prefix):
        n = sum(1 for t in self._tools if getattr(t, "name", "").startswith(prefix))
        self._tools = [t for t in self._tools if not getattr(t, "name", "").startswith(prefix)]
        return n


def _gw(provider=None, servers=None, service=None):
    cfg = Config()
    cfg.mcp_servers = list(servers or [])
    gw = Gateway(config=cfg, service=service or FakeService())
    gw.mcp_provider = provider
    return gw


# ── mcp.add ──────────────────────────────────────────────────────────────────


def test_add_connects_live_and_persists(monkeypatch):
    monkeypatch.setattr(gw_mod, "_persist_mcp_servers", lambda c: True)  # don't touch the real file
    gw = _gw(provider=FakeProvider())
    out = asyncio.run(gw._mcp_add({"name": "notion", "command": ["npx", "-y", "pkg"]}))
    assert out["added"] and out["tools"] == ["notion__hello", "notion__bye"]
    assert [getattr(t, "name", "") for t in gw.service.added] == ["notion__hello", "notion__bye"]
    assert [s.name for s in gw.config.mcp_servers] == ["notion"]  # registered centrally


def test_add_rejects_missing_command_and_dupes(monkeypatch):
    monkeypatch.setattr(gw_mod, "_persist_mcp_servers", lambda c: True)
    gw = _gw(provider=FakeProvider(), servers=[McpServerConfig(name="notion", command=["x"])])
    assert not asyncio.run(gw._mcp_add({"name": "x"}))["added"]  # no command/url
    assert (
        "already exists" in asyncio.run(gw._mcp_add({"name": "notion", "command": ["y"]}))["error"]
    )


def test_add_reports_connect_failure(monkeypatch):
    monkeypatch.setattr(gw_mod, "_persist_mcp_servers", lambda c: True)

    class Dead:
        async def add_server(self, cfg):
            return []  # couldn't connect

    out = asyncio.run(_gw(provider=Dead())._mcp_add({"name": "z", "command": ["q"]}))
    assert not out["added"] and "could not connect" in out["error"]


# ── mcp.list / remove ────────────────────────────────────────────────────────


def test_list_shows_servers_with_connected_state():
    svc = FakeService([_Tool("notion__hello"), _Tool("read")])
    gw = _gw(
        servers=[
            McpServerConfig(name="notion", command=["x"]),
            McpServerConfig(name="slack", command=["y"]),
        ],
        service=svc,
    )
    out = gw._mcp_list()
    by = {s["name"]: s for s in out["servers"]}
    assert by["notion"]["connected"] is True and by["slack"]["connected"] is False


def test_remove_drops_server_and_tools(monkeypatch):
    monkeypatch.setattr(gw_mod, "_persist_mcp_servers", lambda c: True)
    svc = FakeService([_Tool("notion__hello"), _Tool("read")])
    gw = _gw(servers=[McpServerConfig(name="notion", command=["x"])], service=svc)
    out = gw._mcp_remove({"name": "notion"})
    assert out["removed"] and out["toolsDropped"] == 1
    assert gw.config.mcp_servers == [] and "notion__hello" not in [t.name for t in svc._tools]
    assert not gw._mcp_remove({"name": "ghost"})["removed"]


# ── persistence ──────────────────────────────────────────────────────────────


def test_persist_writes_servers_and_preserves_other_keys(tmp_path, monkeypatch):
    cfgfile = tmp_path / "agentd.config.json"
    cfgfile.write_text(
        json.dumps({"channels": [{"type": "line"}], "webhook_port": 8788}), encoding="utf-8"
    )
    monkeypatch.setenv("AGENTD_CONFIG", str(cfgfile))
    cfg = Config()
    cfg.mcp_servers = [McpServerConfig(name="notion", command=["npx", "pkg"], env={"T": "1"})]
    assert _persist_mcp_servers(cfg) is True
    data = json.loads(cfgfile.read_text(encoding="utf-8"))
    assert data["channels"] == [{"type": "line"}] and data["webhook_port"] == 8788  # preserved
    assert data["mcp_servers"] == [
        {"name": "notion", "transport": "stdio", "command": ["npx", "pkg"], "env": {"T": "1"}}
    ]


def test_server_dict_omits_empty():
    assert _server_dict(McpServerConfig(name="n", url="http://x")) == {
        "name": "n",
        "transport": "stdio",
        "url": "http://x",
    }

"""Platform protocol contract (P0 of the agent-apps platform): hello version negotiation
(advisory — advertise, never reject) and the additive `agentId` on every chat.event broadcast,
so any client (and later, scoped app connections) can filter events by agent without knowing
the server-internal session-key format."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.domain.events import AgentEvent
from agentd.presentation.gateway import PROTOCOL_VERSION, Gateway


def _gw(tmp_path) -> Gateway:
    cfg = SimpleNamespace(
        agent_name="jarvis",
        agent_id="main",
        model="test/model",
        reasoning_effort="medium",
        host="127.0.0.1",
        port=8787,
        workspace=str(tmp_path),
        state_dir=str(tmp_path),
        registry_url="",
    )
    return Gateway(config=cfg, service=None, registry=None)


# ---- hello negotiation -------------------------------------------------------------------
def test_hello_advertises_protocol_and_defaults_compatible(tmp_path):
    out = _gw(tmp_path)._hello()
    assert out["protocol"] == PROTOCOL_VERSION
    assert out["compatible"] is True  # no client info -> assume fine (advisory)


def test_hello_negotiation_flags_newer_client(tmp_path):
    gw = _gw(tmp_path)
    ok = gw._hello({"protocol": PROTOCOL_VERSION, "client": "sdk-js/0.1"})
    assert ok["compatible"] is True
    newer = gw._hello({"protocol": PROTOCOL_VERSION + 99, "client": "future/9"})
    assert newer["compatible"] is False  # advisory flag only — the reply still arrives
    garbage = gw._hello({"protocol": "not-a-number"})
    assert garbage["compatible"] is True  # malformed = treated as absent, never breaks


# ---- agentId on chat.event ---------------------------------------------------------------
class _CapturingWs:
    def __init__(self):
        self.frames: list[str] = []

    async def send(self, frame: str) -> None:
        self.frames.append(frame)


def _broadcast_payload(gw: Gateway, session_key: str, agent_id=None) -> dict:
    ws = _CapturingWs()
    gw.clients.add(ws)
    asyncio.run(gw._broadcast(session_key, "run1", AgentEvent("turn_start", {}), agent_id))
    return json.loads(ws.frames[0])["payload"]


def test_broadcast_carries_explicit_agent_id(tmp_path):
    payload = _broadcast_payload(_gw(tmp_path), "desk-abc", agent_id="figure-creator")
    assert payload["agentId"] == "figure-creator"
    assert payload["sessionKey"] == "desk-abc" and payload["runId"] == "run1"


def test_broadcast_derives_agent_from_internal_key(tmp_path):
    # sub-agent / channel / heartbeat keys carry the agent in the key: agent:<id>:...
    payload = _broadcast_payload(_gw(tmp_path), "agent:helper:sub:1:xyz")
    assert payload["agentId"] == "helper"


def test_broadcast_falls_back_to_default_agent(tmp_path):
    payload = _broadcast_payload(_gw(tmp_path), "default")
    assert payload["agentId"] == "main"


# =============================== P2: app hosting + scoping ===================================
from urllib.parse import urlsplit  # noqa: E402

from agentd.presentation.gateway import (  # noqa: E402
    APP_SCOPED_METHODS,
    _scoped_event_allowed,
)
from agentd.presentation.protocol import Request  # noqa: E402


def _app_agent_dir(tmp_path, agent_id="demo") -> SimpleNamespace:
    """A minimal app-agent spec: a dir with ui/index.html + ui/app.js and an [app] dict."""
    d = tmp_path / "agents" / agent_id
    (d / "ui").mkdir(parents=True)
    (d / "ui" / "index.html").write_text("<html>demo app</html>", encoding="utf-8")
    (d / "ui" / "app.js").write_text("console.log('hi')", encoding="utf-8")
    (d / "agent.toml").write_text("secret = true", encoding="utf-8")  # must NOT be servable
    return SimpleNamespace(
        id=agent_id,
        name="Demo",
        app={"entry": "ui/index.html", "title": "Demo Console"},
        dir=d,
        workspace=d / "workspace",
        plugins={},
        tools_allow=None,
        tools_deny=(),
    )


def _gw_with_agent(tmp_path, spec) -> Gateway:
    gw = _gw(tmp_path)

    def _get(a, _s=spec):
        if a == _s.id:
            return _s
        raise KeyError(a)

    gw.registry = SimpleNamespace(list_ids=lambda: [spec.id], get=_get)
    return gw


# ---- [app] parsing (file registry) ----------------------------------------------------------
def test_agent_toml_app_section_parsed(tmp_path):
    from agentd.infrastructure.agents.file_registry import FileAgentRegistry

    d = tmp_path / "agents" / "consoleapp"
    (d / "ui").mkdir(parents=True)
    (d / "ui" / "index.html").write_text("<html/>", encoding="utf-8")
    (d / "agent.toml").write_text(
        'name = "Console"\n[app]\ntitle = "Console App"\n', encoding="utf-8"
    )
    cfg = SimpleNamespace(
        state_dir=str(tmp_path / "state"),
        agents_dir=str(tmp_path / "agents"),
        agent_name="jarvis",
        workspace=str(tmp_path),
    )
    reg = FileAgentRegistry(cfg)
    spec = reg.get("consoleapp")
    assert spec.app == {"entry": "ui/index.html", "title": "Console App"}
    assert reg.get("main").app is None  # no [app] -> a plain chat agent


# ---- static /apps/<id>/ serving --------------------------------------------------------------
def test_serve_app_entry_asset_spa_and_redirect(tmp_path):
    spec = _app_agent_dir(tmp_path)
    gw = _gw_with_agent(tmp_path, spec)
    # entry
    r = gw._serve_app(urlsplit("/apps/demo/"))
    assert r.status_code == 200 and b"demo app" in r.body
    assert "text/html" in r.headers["Content-Type"]
    # real asset
    r = gw._serve_app(urlsplit("/apps/demo/app.js"))
    assert r.status_code == 200 and b"console.log" in r.body
    # SPA fallback: extensionless route -> entry
    r = gw._serve_app(urlsplit("/apps/demo/settings/view"))
    assert r.status_code == 200 and b"demo app" in r.body
    # missing real asset -> 404 (not a silent entry)
    assert gw._serve_app(urlsplit("/apps/demo/missing.js")).status_code == 404
    # no trailing slash -> redirect so relative asset urls resolve
    r = gw._serve_app(urlsplit("/apps/demo?token=T"))
    assert r.status_code == 307 and r.headers["Location"] == "/apps/demo/?token=T"


def test_serve_app_guards(tmp_path):
    spec = _app_agent_dir(tmp_path)
    gw = _gw_with_agent(tmp_path, spec)
    # path traversal out of ui/ must never serve (agent.toml is a sibling of ui/)
    assert gw._serve_app(urlsplit("/apps/demo/../agent.toml")).status_code == 404
    assert gw._serve_app(urlsplit("/apps/demo/%2e%2e/agent.toml")).status_code == 404
    # unknown agent / agent without [app]
    assert gw._serve_app(urlsplit("/apps/nope/")).status_code == 404
    plain = SimpleNamespace(id="plain", name="P", app=None, dir=spec.dir)
    assert _gw_with_agent(tmp_path, plain)._serve_app(urlsplit("/apps/plain/")).status_code == 404


# ---- discovery: the app field ----------------------------------------------------------------
def test_agents_list_carries_app_surface(tmp_path):
    spec = _app_agent_dir(tmp_path)
    gw = _gw_with_agent(tmp_path, spec)
    agents = {a["id"]: a for a in gw._agents_list()["agents"]}
    assert agents["demo"]["app"] == {"title": "Demo Console", "url": "/apps/demo/"}
    # a declared [app] whose entry file is MISSING must not advertise
    broken = SimpleNamespace(
        id="broken", name="B", app={"entry": "ui/gone.html", "title": "B"}, dir=spec.dir
    )
    gw2 = _gw_with_agent(tmp_path, broken)
    assert gw2._agents_list()["agents"][0]["app"] is None


# ---- scoped connections: method tier + forced agent ------------------------------------------
def test_scoped_dispatch_denies_host_tier_and_forces_agent(tmp_path):
    gw = _gw(tmp_path)
    denied = asyncio.run(gw._dispatch(Request(id="1", method="config.get", params={}), None, "demo"))
    assert denied.ok is False and "not available to app connections" in denied.payload["error"]
    assert "config.get" not in APP_SCOPED_METHODS
    # a stable-tier method passes, with the scoped agent FORCED onto the params
    req = Request(id="2", method="sessions.list", params={"agentId": "other-agent"})
    ok = asyncio.run(gw._dispatch(req, None, "demo"))
    assert ok.ok is True
    assert req.params["agentId"] == "demo"  # the app cannot act as another agent
    assert ok.payload.get("agentId") == "demo"


# ---- scoped event filtering ------------------------------------------------------------------
def test_scoped_event_policy():
    assert _scoped_event_allowed("chat.event", {"agentId": "demo"}, "demo")
    assert not _scoped_event_allowed("chat.event", {"agentId": "other"}, "demo")
    assert _scoped_event_allowed("sessions.changed", {"agentId": "demo"}, "demo")
    assert _scoped_event_allowed("agents.changed", {}, "demo")
    assert _scoped_event_allowed("notification", {"agentId": ""}, "demo")
    assert not _scoped_event_allowed("marketplace.progress", {"id": "x"}, "demo")
    assert not _scoped_event_allowed("projects.changed", {}, "demo")


def test_send_all_filters_scoped_connections(tmp_path):
    gw = _gw(tmp_path)
    host_ws, app_ws = _CapturingWs(), _CapturingWs()
    gw.clients.update({host_ws, app_ws})
    gw.client_scopes[app_ws] = "demo"
    asyncio.run(gw._broadcast("s1", "r1", AgentEvent("turn_start", {}), "other"))
    asyncio.run(gw._broadcast("s2", "r2", AgentEvent("turn_start", {}), "demo"))
    assert len(host_ws.frames) == 2  # host sees everything
    assert len(app_ws.frames) == 1  # the app sees only its own agent's run
    assert json.loads(app_ws.frames[0])["payload"]["agentId"] == "demo"


# ---- origin gate ------------------------------------------------------------------------------
def _ws_with_origin(origin: str | None, host: str = "127.0.0.1:8787"):
    headers = {"Host": host}
    if origin is not None:
        headers["Origin"] = origin
    return SimpleNamespace(request=SimpleNamespace(path="/", headers=headers))


def test_origin_gate(tmp_path):
    gw = _gw(tmp_path)
    assert gw._origin_allowed(_ws_with_origin(None))  # native client, no Origin
    assert gw._origin_allowed(_ws_with_origin("null"))  # sandboxed/file page
    assert gw._origin_allowed(_ws_with_origin("file://"))  # Electron packaged
    assert gw._origin_allowed(_ws_with_origin("app://jarvis"))  # custom shells
    assert gw._origin_allowed(_ws_with_origin("http://localhost:5173"))  # dev server
    assert gw._origin_allowed(_ws_with_origin("http://127.0.0.1:8787"))  # served app page
    assert gw._origin_allowed(  # cloud: page served from the same host as the gateway
        _ws_with_origin("https://agents.example.com", host="agents.example.com")
    )
    assert not gw._origin_allowed(_ws_with_origin("https://evil.example.com"))


# ---- scoped tools.invoke ----------------------------------------------------------------------
class _FakeTool:
    name = "echo_tool"
    artifact_action = None  # NOT a canvas-action tool — host invoke must refuse it

    async def execute(self, _id, params, _abort):
        from agentd.application.run_context import current_run_context

        ctx = current_run_context()
        return SimpleNamespace(
            content=[SimpleNamespace(text=f"ran-as:{ctx.agent_id if ctx else 'none'}")],
            is_error=False,
            artifacts=[],
        )


def test_tools_invoke_scoped_runs_agent_allowed_tool(tmp_path):
    spec = _app_agent_dir(tmp_path)
    gw = _gw_with_agent(tmp_path, spec)
    setattr(
        gw,
        "service",
        SimpleNamespace(find_tool=lambda n, a=None: _FakeTool() if n == "echo_tool" else None),
    )
    # scoped: allowed (spec.tools_allow=None => all) and runs AS the agent (run context set)
    out = asyncio.run(gw._tools_invoke({"name": "echo_tool"}, "demo"))
    assert out["text"] == "ran-as:demo"
    # host connection: same tool refused (no artifact_action)
    try:
        asyncio.run(gw._tools_invoke({"name": "echo_tool"}, None))
        raise AssertionError("host invoke should have been refused")
    except RuntimeError as e:
        assert "not invokable" in str(e)
    # scoped but the agent DENIES the tool
    spec.tools_deny = ("echo_tool",)
    try:
        asyncio.run(gw._tools_invoke({"name": "echo_tool"}, "demo"))
        raise AssertionError("denied tool should have been refused")
    except RuntimeError as e:
        assert "not available to agent" in str(e)


# ---- agentd app open: entry point mints the URL and AUTO-STARTS the daemon --------------------
def test_app_open_mints_url_and_ensures_daemon(monkeypatch):
    from agentd import lifecycle
    from agentd.cli import rpc
    from agentd.cli.commands import app as app_cmd

    info = lifecycle.GatewayInfo(host="127.0.0.1", port=8787, pid=1, token="TOK")
    ensured = {"called": False}

    def fake_ensure(wait_sec: float = 300.0):
        ensured["called"] = True
        return info, True  # "was not running -> spawned now"

    monkeypatch.setattr(lifecycle, "ensure_running", fake_ensure)
    monkeypatch.setattr(
        rpc,
        "call",
        lambda *_a, **_k: {
            "agents": [{"id": "demo", "app": {"url": "/apps/demo/", "title": "Demo"}}]
        },
    )
    url = app_cmd._mint_url("demo")
    assert ensured["called"] is True  # opening an app STARTS the daemon when needed
    assert url == "http://127.0.0.1:8787/apps/demo/?token=TOK&scope=agent:demo"
    # unknown agent -> a helpful error, not a broken URL
    try:
        app_cmd._mint_url("nope")
        raise AssertionError("unknown agent should error")
    except rpc.RpcError as e:
        assert "no app UI" in str(e)


# =============================== P3: the app-demo agent ships whole =============================
def test_app_demo_bundle_roundtrip_carries_ui(tmp_path):
    """The end-to-end shipping proof: pack the real v2/agents/app-demo (definition + ui/) into an
    .agentpkg, unpack it into a fresh agents_dir (= install), and the app agent works from there —
    [app] parsed, ui served. 'Anyone can ship a child client' as one file."""
    from agentd.domain.bundle import BundleManifest
    from agentd.infrastructure.marketplace.bundle_io import (
        pack_bundle,
        read_manifest,
        unpack_bundle,
    )

    src = Path(__file__).resolve().parents[1] / "agents" / "app-demo"
    assert (src / "ui" / "index.html").is_file(), "the reference app agent must ship a ui/"

    manifest = BundleManifest(
        id="app-demo", name="App Demo", version="1.0.0", description="reference agent app"
    )
    pkg = pack_bundle(src, tmp_path / "out", manifest)
    assert read_manifest(pkg).id == "app-demo"

    agents_dir = tmp_path / "agents"
    unpack_bundle(pkg, manifest, agents_dir, tmp_path / "plugins")
    installed = agents_dir / "app-demo"
    assert (installed / "agent.toml").is_file()
    assert (installed / "ui" / "index.html").is_file()  # the UI travelled inside the package
    assert (installed / "ui" / "vendor" / "agentd-client.js").is_file()  # SDK too

    # the installed copy is a working app agent: registry parses [app], the gateway serves it
    from agentd.infrastructure.agents.file_registry import FileAgentRegistry

    cfg = SimpleNamespace(
        state_dir=str(tmp_path / "state"),
        agents_dir=str(agents_dir),
        agent_name="jarvis",
        workspace=str(tmp_path),
    )
    spec = FileAgentRegistry(cfg).get("app-demo")
    assert spec.app == {"entry": "ui/index.html", "title": "App Demo Console"}
    gw = _gw_with_agent(tmp_path, spec)
    assert gw._agent_app("app-demo", spec) == {
        "title": "App Demo Console",
        "url": "/apps/app-demo/",
    }
    r = gw._serve_app(urlsplit("/apps/app-demo/"))
    assert r.status_code == 200 and b"App Demo Console" in r.body

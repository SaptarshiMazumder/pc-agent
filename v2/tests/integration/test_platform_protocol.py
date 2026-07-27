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
    # no mode declared -> browser (the default presentation); no public opt-in -> private
    assert spec.app == {
        "entry": "ui/index.html",
        "title": "Console App",
        "mode": "browser",
        "public": False,
        "public_tools": (),
    }
    assert reg.get("main").app is None  # no [app] -> a plain chat agent


def test_agent_toml_app_mode_declared_and_normalized(tmp_path):
    """[app] mode: the AUTHOR declares how openers present the app — "window" (its own
    chromeless window) or "browser" (a tab, the default); junk falls back to browser."""
    from agentd.infrastructure.agents.file_registry import FileAgentRegistry

    agents = tmp_path / "agents"
    for aid, mode_line in (("winapp", 'mode = "window"'), ("junkapp", 'mode = "kiosk"')):
        d = agents / aid
        (d / "ui").mkdir(parents=True)
        (d / "ui" / "index.html").write_text("<html/>", encoding="utf-8")
        (d / "agent.toml").write_text(f'name = "X"\n[app]\n{mode_line}\n', encoding="utf-8")
    cfg = SimpleNamespace(
        state_dir=str(tmp_path / "state"),
        agents_dir=str(agents),
        agent_name="jarvis",
        workspace=str(tmp_path),
    )
    reg = FileAgentRegistry(cfg)
    assert reg.get("winapp").app["mode"] == "window"
    assert reg.get("junkapp").app["mode"] == "browser"  # unknown value -> safe default


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
    assert agents["demo"]["app"] == {
        "title": "Demo Console",
        "url": "/apps/demo/",
        "mode": "browser",
    }
    # a declared [app] whose entry file is MISSING must not advertise
    broken = SimpleNamespace(
        id="broken", name="B", app={"entry": "ui/gone.html", "title": "B"}, dir=spec.dir
    )
    gw2 = _gw_with_agent(tmp_path, broken)
    assert gw2._agents_list()["agents"][0]["app"] is None


# ---- scoped connections: method tier + forced agent ------------------------------------------
def test_scoped_dispatch_denies_host_tier_and_forces_agent(tmp_path):
    gw = _gw(tmp_path)
    denied = asyncio.run(
        gw._dispatch(Request(id="1", method="config.get", params={}), None, "demo")
    )
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
    gw.service = SimpleNamespace(
        find_tool=lambda n, a=None: _FakeTool() if n == "echo_tool" else None
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
    url, app = app_cmd._mint_url("demo")
    assert ensured["called"] is True  # opening an app STARTS the daemon when needed
    # exact prefix + a per-launch fresh= cache-buster (value varies, so match it loosely)
    assert url.startswith("http://127.0.0.1:8787/apps/demo/?token=TOK&scope=agent:demo&fresh=")
    assert app == {"url": "/apps/demo/", "title": "Demo"}  # descriptor rides along (mode etc.)
    # unknown agent -> a helpful error, not a broken URL
    try:
        app_cmd._mint_url("nope")
        raise AssertionError("unknown agent should error")
    except rpc.RpcError as e:
        assert "no app UI" in str(e)


def test_app_open_window_prefers_app_window_and_falls_back(monkeypatch):
    """`agentd app open`: flags force a presentation; else the AGENT's declared [app] mode
    decides. A chromeless --app= window needs a Chromium-family browser; otherwise it falls
    back to a plain tab (never a hard failure). AGENTD_APP_BROWSER overrides discovery."""
    import argparse
    import subprocess
    import webbrowser

    from agentd.cli.commands import app as app_cmd

    URL = "http://127.0.0.1:8787/apps/demo/?x=1"
    real_find = app_cmd._find_chromium  # keep the real one for the env-override check
    monkeypatch.setattr(app_cmd, "_mint_url", lambda _a: (URL, {"mode": "browser"}))
    calls = {"popen": None, "webbrowser": None}

    class _FakePopen:
        def __init__(self, argv):
            calls["popen"] = argv

    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(webbrowser, "open", lambda url: calls.__setitem__("webbrowser", url))

    # --window forces the app window even for a browser-mode agent
    monkeypatch.setattr(app_cmd, "_find_chromium", lambda: r"C:\fake\msedge.exe")
    args = argparse.Namespace(agent_id="demo", window=True, browser=False)
    assert app_cmd.run_open(args) == 0
    assert calls["popen"] == [r"C:\fake\msedge.exe", f"--app={URL}"]
    assert calls["webbrowser"] is None

    # NO browser found -> graceful fallback to the default browser
    calls["popen"] = None
    monkeypatch.setattr(app_cmd, "_find_chromium", lambda: "")
    assert app_cmd.run_open(args) == 0
    assert calls["popen"] is None
    assert calls["webbrowser"] == URL

    # the agent DECLARED mode = "window": no flags needed, the app window opens
    monkeypatch.setattr(app_cmd, "_mint_url", lambda _a: (URL, {"mode": "window"}))
    monkeypatch.setattr(app_cmd, "_find_chromium", lambda: r"C:\fake\msedge.exe")
    calls["popen"] = calls["webbrowser"] = None
    assert app_cmd.run_open(argparse.Namespace(agent_id="demo", window=False, browser=False)) == 0
    assert calls["popen"] == [r"C:\fake\msedge.exe", f"--app={URL}"]

    # --browser overrides the declared window mode
    calls["popen"] = calls["webbrowser"] = None
    assert app_cmd.run_open(argparse.Namespace(agent_id="demo", window=False, browser=True)) == 0
    assert calls["popen"] is None and calls["webbrowser"] == URL

    # the env override wins discovery (an existing path is used as-is)
    monkeypatch.setenv("AGENTD_APP_BROWSER", __file__)
    assert real_find() == __file__


def test_agents_create_scaffolds_app_agent(tmp_path):
    """registry.create(app="window"): the new agent ships [app] mode="window" + a starter
    ui/ page — an OPENABLE app agent the moment it exists, advertised with its mode."""
    from agentd.infrastructure.agents.file_registry import FileAgentRegistry

    cfg = SimpleNamespace(
        state_dir=str(tmp_path / "state"),
        agents_dir=str(tmp_path / "agents"),
        agent_name="jarvis",
        workspace=str(tmp_path),
    )
    reg = FileAgentRegistry(cfg)
    spec = reg.create("hr-desk", name="HR Desk", app="window")
    assert spec.app == {
        "entry": "ui/index.html",
        "title": "HR Desk",
        "mode": "window",
        "public": False,  # scaffolded agents are always private until the author opts in
        "public_tools": (),
    }
    entry = Path(cfg.agents_dir) / "hr-desk" / "ui" / "index.html"
    assert entry.is_file() and "HR Desk" in entry.read_text(encoding="utf-8")
    # advertised WITH the mode (entry exists), so every opener knows how to present it
    assert Gateway._agent_app("hr-desk", spec) == {
        "title": "HR Desk",
        "url": "/apps/hr-desk/",
        "mode": "window",
    }
    # a plain create stays a chat agent (no [app], no ui/)
    assert reg.create("plain", name="Plain").app is None


# =============================== P3: an app agent ships whole ===================================
def test_app_agent_bundle_roundtrip_carries_ui(tmp_path):
    """The end-to-end shipping proof on a SYNTHETIC app agent: pack a definition + ui/ into an
    .agentpkg, unpack it into a fresh agents_dir (= install), and the app agent works from there —
    [app] parsed (mode included), ui served. Built products (clients/) and user data never ride
    inside the package. 'Anyone can ship a child client' as one file."""
    from agentd.domain.bundle import BundleManifest
    from agentd.infrastructure.marketplace.bundle_io import (
        pack_bundle,
        read_manifest,
        unpack_bundle,
    )

    src = tmp_path / "src" / "kiosk"
    (src / "ui").mkdir(parents=True)
    (src / "ui" / "index.html").write_text("<html>Kiosk Console</html>", encoding="utf-8")
    (src / "ui" / "app.js").write_text("console.log('kiosk')", encoding="utf-8")
    (src / "agent.toml").write_text(
        'name = "Kiosk"\n[app]\ntitle = "Kiosk Console"\nmode = "window"\n', encoding="utf-8"
    )
    # a delivered BUILT PRODUCT sitting in the agent's folder — must NOT pack (it is
    # derived from this very package; nesting it would be product-inside-source)
    (src / "clients" / "desktop").mkdir(parents=True)
    (src / "clients" / "desktop" / "Kiosk Setup.exe").write_bytes(b"MZ fake installer")

    manifest = BundleManifest(id="kiosk", name="Kiosk", version="1.0.0", description="app agent")
    pkg = pack_bundle(src, tmp_path / "out", manifest)
    assert read_manifest(pkg).id == "kiosk"

    agents_dir = tmp_path / "agents"
    unpack_bundle(pkg, manifest, agents_dir, tmp_path / "plugins")
    installed = agents_dir / "kiosk"
    assert (installed / "agent.toml").is_file()
    assert (installed / "ui" / "index.html").is_file()  # the UI travelled inside the package
    assert (installed / "ui" / "app.js").is_file()
    assert not (installed / "clients").exists()  # built products never ship in the source artifact

    # the installed copy is a working app agent: registry parses [app], the gateway serves it
    from agentd.infrastructure.agents.file_registry import FileAgentRegistry

    cfg = SimpleNamespace(
        state_dir=str(tmp_path / "state"),
        agents_dir=str(agents_dir),
        agent_name="jarvis",
        workspace=str(tmp_path),
    )
    spec = FileAgentRegistry(cfg).get("kiosk")
    assert spec.app == {
        "entry": "ui/index.html",
        "title": "Kiosk Console",
        "mode": "window",
        "public": False,
        "public_tools": (),
    }
    gw = _gw_with_agent(tmp_path, spec)
    assert gw._agent_app("kiosk", spec) == {
        "title": "Kiosk Console",
        "url": "/apps/kiosk/",
        "mode": "window",
    }
    r = gw._serve_app(urlsplit("/apps/kiosk/"))
    assert r.status_code == 200 and b"Kiosk Console" in r.body


# =========================== P3: public app access + host aliases =============================
# Hosted deployments: an agent whose [app] declares `public = true` admits UNAUTHENTICATED
# connections scoped to it, limited to PUBLIC_APP_METHODS and the author-declared
# `public_tools` subset. config.app_hosts maps a vanity hostname to an agent so each curated
# agent lives at its own URL. Both are fully dormant by default (no opt-in, empty map).
from agentd.presentation.gateway import PUBLIC_APP_METHODS  # noqa: E402


def test_agent_toml_app_public_parsed(tmp_path):
    from agentd.infrastructure.agents.file_registry import FileAgentRegistry

    d = tmp_path / "agents" / "pubapp"
    (d / "ui").mkdir(parents=True)
    (d / "ui" / "index.html").write_text("<html/>", encoding="utf-8")
    (d / "agent.toml").write_text(
        'name = "Pub"\n[app]\npublic = true\npublic_tools = ["get_weather", " ", ""]\n',
        encoding="utf-8",
    )
    cfg = SimpleNamespace(
        state_dir=str(tmp_path / "state"),
        agents_dir=str(tmp_path / "agents"),
        agent_name="jarvis",
        workspace=str(tmp_path),
    )
    spec = FileAgentRegistry(cfg).get("pubapp")
    assert spec.app["public"] is True
    assert spec.app["public_tools"] == ("get_weather",)  # blanks dropped


def _public_app_spec(tmp_path, public_tools=("echo_tool",)):
    spec = _app_agent_dir(tmp_path)
    spec.app["public"] = True
    spec.app["public_tools"] = tuple(public_tools)
    return spec


def test_public_scope_ok_requires_app_opt_in(tmp_path):
    spec = _public_app_spec(tmp_path)
    gw = _gw_with_agent(tmp_path, spec)
    assert gw._public_scope_ok("demo") is True
    spec.app["public"] = False
    assert gw._public_scope_ok("demo") is False  # not opted in
    assert gw._public_scope_ok("nope") is False  # unknown agent
    assert gw._public_scope_ok(None) is False  # unscoped never public
    gw.registry = None
    assert gw._public_scope_ok("demo") is False  # no registry (bare gateway)


class _ConnWs:
    """Just enough of a ServerConnection for _handle_conn: a request, a close(), an
    async iterator that ends immediately (the connection opens then hangs up)."""

    def __init__(self, path="/", host="127.0.0.1:8787"):
        self.request = SimpleNamespace(path=path, headers={"Host": host})
        self.closed: tuple | None = None

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


def test_unauthenticated_conn_admitted_only_for_public_scope(tmp_path):
    spec = _public_app_spec(tmp_path)
    gw = _gw_with_agent(tmp_path, spec)
    gw.auth_token = "secret"  # hosted daemon: auth ON, none of these present a token

    ws = _ConnWs(path="/?scope=agent:demo")
    asyncio.run(gw._handle_conn(ws))
    assert ws.closed is None  # admitted (public tier), then hung up normally
    assert ws not in gw.client_public  # and cleaned up on disconnect

    bare = _ConnWs(path="/")
    asyncio.run(gw._handle_conn(bare))
    assert bare.closed is not None and bare.closed[0] == 4401  # unscoped -> refused

    spec.app["public"] = False
    private = _ConnWs(path="/?scope=agent:demo")
    asyncio.run(gw._handle_conn(private))
    assert private.closed is not None and private.closed[0] == 4401  # no opt-in -> refused


def test_public_dispatch_is_a_strict_subset_of_the_scoped_tier(tmp_path):
    gw = _gw(tmp_path)
    assert PUBLIC_APP_METHODS < APP_SCOPED_METHODS  # subset by construction
    for method in ("chat.send", "sessions.list", "workspace.list", "config.get"):
        r = asyncio.run(gw._dispatch(Request(id="1", method=method, params={}), None, "demo", True))
        assert r.ok is False and "public" in r.payload["error"]
    # the same scoped connection WITH auth (public=False) still reaches the stable tier
    ok = asyncio.run(
        gw._dispatch(Request(id="2", method="sessions.list", params={}), None, "demo", False)
    )
    assert ok.ok is True


def test_tools_invoke_public_filter(tmp_path):
    spec = _public_app_spec(tmp_path, public_tools=("echo_tool",))
    gw = _gw_with_agent(tmp_path, spec)

    class _MailTool(_FakeTool):
        name = "send_email"

    tools = {"echo_tool": _FakeTool(), "send_email": _MailTool()}
    gw.service = SimpleNamespace(find_tool=lambda n, a=None: tools.get(n))

    # public: the declared tool runs AS the agent…
    out = asyncio.run(gw._tools_invoke({"name": "echo_tool"}, "demo", True))
    assert out["text"] == "ran-as:demo"
    # …but anything OUTSIDE public_tools is refused, even though the AGENT is allowed it
    try:
        asyncio.run(gw._tools_invoke({"name": "send_email"}, "demo", True))
        raise AssertionError("public invoke of a non-public tool should have been refused")
    except RuntimeError as e:
        assert "not publicly invokable" in str(e)
    # an authenticated scoped connection is untouched by the public filter
    out = asyncio.run(gw._tools_invoke({"name": "send_email"}, "demo", False))
    assert out["text"] == "ran-as:demo"


def test_host_alias_serving_scope_and_dormancy(tmp_path):
    spec = _app_agent_dir(tmp_path)
    gw = _gw_with_agent(tmp_path, spec)
    gw.config.app_hosts = {"demo.example.com": "demo"}

    # aliased host serves the agent's ui at "/" (and assets by path)
    r = gw._http_request(None, SimpleNamespace(path="/", headers={"Host": "demo.example.com"}))
    assert r is not None and r.status_code == 200 and b"demo app" in r.body
    r = gw._http_request(
        None, SimpleNamespace(path="/app.js", headers={"Host": "demo.example.com"})
    )
    assert r is not None and r.status_code == 200 and b"console.log" in r.body

    # a WebSocket upgrade on the aliased host must NOT be short-circuited
    ws_req = SimpleNamespace(path="/", headers={"Host": "demo.example.com", "Upgrade": "websocket"})
    assert gw._http_request(None, ws_req) is None

    # …and the connection it becomes is scoped to the aliased agent server-side
    ws = SimpleNamespace(request=ws_req)
    assert gw._connection_scope(ws) == "demo"
    # explicit scope always wins over the alias
    explicit = SimpleNamespace(
        request=SimpleNamespace(path="/?scope=agent:other", headers={"Host": "demo.example.com"})
    )
    assert gw._connection_scope(explicit) == "other"

    # unaliased host: nothing changes (falls through to the normal handshake / no scope)
    plain = SimpleNamespace(path="/", headers={"Host": "127.0.0.1:8787"})
    assert gw._http_request(None, plain) is None
    assert gw._connection_scope(SimpleNamespace(request=plain)) is None

    # dormant by default: empty map == today's behavior everywhere
    gw.config.app_hosts = {}
    assert (
        gw._http_request(None, SimpleNamespace(path="/", headers={"Host": "demo.example.com"}))
        is None
    )

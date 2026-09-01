import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_runtime.config import McpServerConfig, load_config
from agent_runtime.domain.mcp import McpCallResult, McpToolSpec
from agent_runtime.domain.messages import TextContent
from agent_runtime.infrastructure.tools.guard import GuardedTool
from agent_runtime.infrastructure.tools.mcp.factory import build_mcp_provider
from agent_runtime.infrastructure.tools.mcp.mapping import to_tool_result
from agent_runtime.infrastructure.tools.mcp.provider import McpProvider
from agent_runtime.infrastructure.tools.mcp.tool import McpTool

# ---- fakes (satisfy the McpSession protocol structurally) -----------------


class FakeSession:
    def __init__(self, name, specs=None, call=None, closed_log=None):
        self.name = name
        self._specs = specs or []
        self._call = call
        self.closed_log = closed_log if closed_log is not None else []

    async def list_tools(self):
        return self._specs

    async def call_tool(self, name, arguments):
        if self._call:
            return self._call(name, arguments)
        return McpCallResult(content=[TextContent(text=f"ok:{name}:{arguments}")])

    async def close(self):
        self.closed_log.append(self.name)


def _spec(server, name, schema=None):
    return McpToolSpec(
        server=server,
        name=name,
        description=f"{name} desc",
        input_schema=schema or {"type": "object", "properties": {}},
    )


def _provider(cfg, session):
    async def factory(_cfg):
        if isinstance(session, Exception):
            raise session
        return session

    return McpProvider([cfg], factory)


# ---- McpTool ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcptool_success_maps_to_toolresult():
    sess = FakeSession("g")
    t = McpTool(sess, _spec("g", "echo"), "g__echo")
    res = await t.execute("c", {"x": 1}, asyncio.Event())
    assert res.is_error is False
    assert "ok:echo:{'x': 1}" in res.content[0].text


@pytest.mark.asyncio
async def test_mcptool_error_flag_propagates():
    sess = FakeSession(
        "g", call=lambda n, a: McpCallResult(content=[TextContent(text="boom")], is_error=True)
    )
    res = await McpTool(sess, _spec("g", "x"), "g__x").execute("c", {}, asyncio.Event())
    assert res.is_error is True and res.content[0].text == "boom"


@pytest.mark.asyncio
async def test_mcptool_never_raises_on_exception():
    def boom(n, a):
        raise ConnectionError("server gone")

    res = await McpTool(FakeSession("g", call=boom), _spec("g", "x"), "g__x").execute(
        "c", {}, asyncio.Event()
    )
    assert res.is_error is True and "ConnectionError" in res.content[0].text


@pytest.mark.asyncio
async def test_mcptool_reraises_cancellation():
    def cancel(n, a):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await McpTool(FakeSession("g", call=cancel), _spec("g", "x"), "g__x").execute(
            "c", {}, asyncio.Event()
        )


def test_mcptool_schema_and_policy_attrs():
    schema = {"type": "object", "required": ["q"], "properties": {"q": {"type": "string"}}}
    t = McpTool(FakeSession("g"), _spec("g", "search", schema), "g__search")
    assert t.parameters == schema  # MCP inputSchema is JSON Schema -> drop-in
    assert t.name == "g__search"
    assert McpTool.default_retryable is False and McpTool.default_timeout_sec == 120.0


# ---- McpProvider -----------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_namespaces_tools():
    cfg = McpServerConfig(name="google", command=["x"])
    sess = FakeSession(
        "google", specs=[_spec("google", "gmail_send"), _spec("google", "drive_list")]
    )
    tools = await _provider(cfg, sess).discover()
    assert [t.name for t in tools] == ["google__gmail_send", "google__drive_list"]


@pytest.mark.asyncio
async def test_discover_allowlist_filters():
    cfg = McpServerConfig(name="g", command=["x"], allow=["a"])
    sess = FakeSession("g", specs=[_spec("g", "a"), _spec("g", "b")])
    tools = await _provider(cfg, sess).discover()
    assert [t.name for t in tools] == ["g__a"]


@pytest.mark.asyncio
async def test_discover_server_down_is_graceful():
    cfg = McpServerConfig(name="dead", command=["x"])
    tools = await _provider(cfg, ConnectionError("cannot connect")).discover()
    assert tools == []  # skipped, no raise


@pytest.mark.asyncio
async def test_discover_disabled_server_skipped():
    cfg = McpServerConfig(name="g", command=["x"], enabled=False)
    tools = await _provider(cfg, FakeSession("g", specs=[_spec("g", "a")])).discover()
    assert tools == []


@pytest.mark.asyncio
async def test_aclose_closes_all_sessions():
    log = []
    cfg = McpServerConfig(name="g", command=["x"])
    sess = FakeSession("g", specs=[_spec("g", "a")], closed_log=log)
    prov = _provider(cfg, sess)
    await prov.discover()
    await prov.aclose()
    assert log == ["g"]


# ---- mapping ---------------------------------------------------------------


def test_mapping_empty_content_placeholder():
    res = to_tool_result(McpCallResult(content=[]))
    assert res.content[0].text == "(no content)" and res.is_error is False


# ---- factory ---------------------------------------------------------------


def test_factory_none_without_servers():
    assert build_mcp_provider(SimpleNamespace(mcp_servers=[])) is None


def test_factory_supports_http_transport():
    cfg = SimpleNamespace(mcp_servers=[McpServerConfig(name="h", transport="http", url="http://x")])
    # streamable-HTTP is now supported (e.g. Parallel's hosted Search MCP)
    assert build_mcp_provider(cfg) is not None


def test_factory_skips_unknown_transport():
    cfg = SimpleNamespace(
        mcp_servers=[McpServerConfig(name="w", transport="websocket", url="ws://x")]
    )
    assert build_mcp_provider(cfg) is None


# ---- subprocess env (inherit .env + ${VAR} expansion) ----------------------


def test_resolve_subprocess_inherits_and_expands(monkeypatch):
    from agent_runtime.infrastructure.tools.mcp.session import resolve_subprocess

    monkeypatch.setenv("MY_CLIENT_ID", "abc123")  # e.g. a value from .env
    monkeypatch.setenv("INHERITED_VAR", "yes")
    cfg = McpServerConfig(
        name="g",
        command=["uvx", "workspace-mcp"],
        env={"GOOGLE_OAUTH_CLIENT_ID": "${MY_CLIENT_ID}", "FLAG": "1"},
    )
    cmd, env = resolve_subprocess(cfg)
    # The launcher is resolved to an ABSOLUTE path when we can find it. Handing the child a
    # PATH is not enough — the OS resolves the executable using the SPAWNING process's PATH,
    # so a pip-installed `uvx` in our own Scripts dir failed with "file not found" no matter
    # what PATH we passed. Args are untouched.
    assert cmd[0].lower().endswith(("uvx", "uvx.exe")) or cmd[0] == "uvx"
    assert cmd[1:] == ["workspace-mcp"]
    assert env["GOOGLE_OAUTH_CLIENT_ID"] == "abc123"  # ${VAR} expanded from env
    assert env["FLAG"] == "1"  # literal overlay
    assert env["INHERITED_VAR"] == "yes"  # full env inherited (secrets in .env reach the server)


def test_resolve_subprocess_finds_a_launcher_installed_beside_us(tmp_path, monkeypatch):
    """A launcher declared in pyproject lands in this interpreter's script dir, which is NOT
    on PATH just because the interpreter is running. It must still be found."""
    import os
    import sys
    import sysconfig

    from agent_runtime.infrastructure.tools.mcp.session import resolve_subprocess

    scripts = sysconfig.get_path("scripts")
    name = "zz-fake-launcher" + (".exe" if sys.platform.startswith("win") else "")
    fake = os.path.join(scripts, name)
    with open(fake, "wb") as f:  # contents irrelevant; only resolution is under test
        f.write(b"")
    monkeypatch.setenv("PATH", str(tmp_path))  # ambient PATH deliberately lacks it
    try:
        cmd, _ = resolve_subprocess(McpServerConfig(name="x", command=["zz-fake-launcher", "--go"]))
        # case-insensitive: on Windows shutil.which returns the extension as PATHEXT spells
        # it (".EXE"), not as the file was created
        assert cmd[0].lower() == fake.lower(), "a launcher beside the interpreter must resolve"
        assert cmd[1:] == ["--go"]
    finally:
        os.remove(fake)


def test_resolve_subprocess_leaves_an_unknown_command_alone(monkeypatch, tmp_path):
    """When the launcher genuinely is not installed, pass the name through untouched so the
    spawn error names what was missing. Substituting something else would hide the cause."""
    from agent_runtime.infrastructure.tools.mcp.session import resolve_subprocess

    monkeypatch.setenv("PATH", str(tmp_path))
    cmd, _ = resolve_subprocess(McpServerConfig(name="x", command=["definitely-not-installed"]))
    assert cmd == ["definitely-not-installed"]


# ---- config conversion -----------------------------------------------------


def test_config_coerces_mcp_servers_dicts(tmp_path, monkeypatch):
    cfgfile = tmp_path / "agentd.config.json"
    cfgfile.write_text(
        json.dumps(
            {
                "mcp_servers": [
                    {"name": "google", "command": ["uvx", "workspace-mcp"], "env": {"K": "v"}},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENTD_CONFIG", str(cfgfile))
    cfg = load_config()
    assert len(cfg.mcp_servers) == 1
    s = cfg.mcp_servers[0]
    assert isinstance(s, McpServerConfig)
    assert s.name == "google" and s.command == ["uvx", "workspace-mcp"] and s.transport == "stdio"


# ---- gateway wiring (discovered MCP tools get guarded + added) -------------


@pytest.mark.asyncio
async def test_gateway_discovers_and_guards_mcp_tools():
    from agent_runtime.main.container import build_service

    svc = build_service(load_config(), None, None)
    before = len(svc._tools)

    class FakeProvider:
        async def discover(self):
            sess = FakeSession("g", specs=[_spec("g", "ping")])
            return [McpTool(sess, _spec("g", "ping"), "g__ping")]

        async def aclose(self):
            pass

    from agent_runtime.presentation.gateway import Gateway

    gw = Gateway(config=load_config(), service=svc, mcp_provider=FakeProvider())
    await gw._discover_mcp_tools()

    names = [t.name for t in svc._tools]
    assert "g__ping" in names and len(svc._tools) == before + 1
    added = next(t for t in svc._tools if t.name == "g__ping")
    assert isinstance(added, GuardedTool)  # MCP tools flow through the same guard

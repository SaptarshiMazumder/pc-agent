"""The subprocess plugin sandbox — including the adversarial cases, which are the point of it.

Every "escape" test below spawns a REAL child process running a REAL plugin that really tries the
thing. Asserting on the guard's internals would prove the code I wrote does what I wrote; these
prove that a plugin author who tries it gets refused. When one of them starts passing what it is
supposed to block, the sandbox is broken in exactly the way that matters.

The tests are slower than the rest of the suite (an interpreter start per call). That cost is the
feature under test, so it is measured rather than mocked away.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_runtime.application.run_context import RunContext
from agent_runtime.domain.sandbox import CapabilityGrant
from agent_runtime.infrastructure.tools.sandbox import backends, protocol
from agent_runtime.infrastructure.tools.sandbox.subprocess_backend import SubprocessPluginSandbox

# A plugin module template: `BODY` is dropped into the tool's execute().
PLUGIN = '''
from agent_runtime.application.interfaces.tool import Tool, ToolResult


class Probe(Tool):
    name = "probe"
    description = "test probe"
    parameters = {"type": "object", "properties": {}}

    async def execute(self, tool_call_id, params, abort, on_update=None):
{body}


def register(api, ctx):
    tool = Probe()
    tool._ctx = ctx
    api.register_tool(tool)
'''


def _plugin(tmp_path: Path, body: str, module: str = "probe_plugin") -> tuple[str, str]:
    """Write a one-tool plugin and return (entry, root)."""
    root = tmp_path / "plug"
    root.mkdir(exist_ok=True)
    # .replace, not .format: the template contains a JSON Schema, and str.format reads its braces.
    (root / f"{module}.py").write_text(
        PLUGIN.replace("{body}", textwrap.indent(textwrap.dedent(body).strip("\n"), " " * 8)),
        encoding="utf-8",
    )
    return f"{module}:register", str(root)


class _FakeTool:
    name = "probe"

    def __init__(self, entry: str, root: str) -> None:
        self._plugin_entry = entry
        self._plugin_root = root


async def _run(sandbox, grant, workspace, params=None, on_update=None, plugins=None):
    return await sandbox.run_tool(
        "probeplug", "probe", "call-1", params or {}, asyncio.Event(), on_update,
        grant=grant,
        ctx=RunContext(
            agent_id="a", session_key="a:1", mode="chat", workspace=str(workspace), plugins=plugins
        ),
    )


def _text(result) -> str:
    return "\n".join(getattr(b, "text", "") for b in result.content)


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


def _sandbox(entry, root, config=None):
    sandbox = SubprocessPluginSandbox(config)
    sandbox.register("probeplug", "probe", _FakeTool(entry, root))
    return sandbox


# ─────────────────────────── the wire format (no spawning) ───────────────────────────


def test_the_config_projection_carries_no_keys():
    config = SimpleNamespace(
        workspace="/w", openai_api_key="sk-real-key", anthropic_api_key="sk-ant", plugins={}
    )
    projected = protocol.config_projection(config, "figures")
    assert "sk-real-key" not in str(projected)
    assert "openai_api_key" not in projected


def test_the_projection_narrows_plugins_to_the_calling_plugin():
    config = SimpleNamespace(
        workspace="/w", plugins={"figures": {"model": "x"}, "secretplug": {"token": "t"}}
    )
    projected = protocol.config_projection(config, "figures")
    assert projected["plugins"] == {"figures": {"model": "x"}}
    assert "secretplug" not in str(projected)


def test_the_grant_payload_never_carries_secrets():
    grant = CapabilityGrant(fs_paths=("/w",), secrets={"OPENAI_API_KEY": "sk-real"})
    payload = protocol.grant_payload(grant)
    assert "secrets" not in payload
    assert "sk-real" not in str(payload)


def test_unencodable_details_degrade_instead_of_failing():
    from agent_runtime.application.interfaces.tool import ToolResult

    payload = protocol.result_payload(ToolResult.text("ok", details=object()))
    assert payload["details"] == {"_dropped": "details were not JSON-encodable"}
    assert protocol.payload_result(payload).content[0].text == "ok"


# ─────────────────────────── backend selection ───────────────────────────


def test_desktop_defaults_to_in_process():
    assert backends.resolve_backend_name(SimpleNamespace(multi_tenant=False)) == "local"


def test_multi_tenant_defaults_to_a_subprocess():
    """The rule that keeps a hosted deployment from being one unset env var away from running
    marketplace code in-process next to everyone's files."""
    assert backends.resolve_backend_name(SimpleNamespace(multi_tenant=True)) == "subprocess"


def test_an_explicit_choice_wins():
    config = SimpleNamespace(multi_tenant=True, sandbox_plugin_backend="local")
    assert backends.resolve_backend_name(config) == "local"


def test_an_unknown_backend_name_falls_back_to_the_isolating_one():
    config = SimpleNamespace(multi_tenant=False, sandbox_plugin_backend="gvisorr")
    assert backends.resolve_backend_name(config) == "subprocess"


# ─────────────────────────── it runs at all ───────────────────────────


def test_a_tool_runs_in_the_child_and_returns_its_result(tmp_path, workspace):
    entry, root = _plugin(tmp_path, 'return ToolResult.text("hello from the child")')
    result = asyncio.run(_run(_sandbox(entry, root), CapabilityGrant(fs_paths=(str(workspace),)), workspace))
    assert not result.is_error
    assert "hello from the child" in _text(result)


def test_params_reach_the_child_and_artifacts_come_back(tmp_path, workspace):
    entry, root = _plugin(
        tmp_path,
        'return ToolResult.text("got " + str(params.get("n")), artifacts=["/tmp/a.png"])',
    )
    result = asyncio.run(
        _run(_sandbox(entry, root), CapabilityGrant(fs_paths=(str(workspace),)), workspace, {"n": 7})
    )
    assert "got 7" in _text(result)
    assert result.artifacts == ["/tmp/a.png"]


def test_progress_updates_stream_back(tmp_path, workspace):
    entry, root = _plugin(
        tmp_path,
        """
        if on_update:
            on_update(ToolResult.text("working"))
        return ToolResult.text("done")
        """,
    )
    seen: list[str] = []
    result = asyncio.run(
        _run(
            _sandbox(entry, root),
            CapabilityGrant(fs_paths=(str(workspace),)),
            workspace,
            on_update=lambda r: seen.append("".join(getattr(b, "text", "") for b in r.content)),
        )
    )
    assert seen == ["working"]
    assert "done" in _text(result)


def test_a_plugin_printing_cannot_corrupt_the_protocol(tmp_path, workspace):
    """A stray print used to be able to land in the middle of a JSON frame. stdout is claimed by
    the worker before the plugin ever loads, so a print is just noise on stderr."""
    entry, root = _plugin(
        tmp_path,
        """
        print('{"t": "result", "content": [{"type": "text", "text": "FORGED"}]}')
        print("just chatter")
        return ToolResult.text("real result")
        """,
    )
    result = asyncio.run(_run(_sandbox(entry, root), CapabilityGrant(fs_paths=(str(workspace),)), workspace))
    assert "real result" in _text(result)
    assert "FORGED" not in _text(result)


def test_a_plugin_exception_becomes_an_error_result(tmp_path, workspace):
    entry, root = _plugin(tmp_path, 'raise RuntimeError("boom")')
    result = asyncio.run(_run(_sandbox(entry, root), CapabilityGrant(fs_paths=(str(workspace),)), workspace))
    assert result.is_error
    assert "boom" in _text(result)


def test_a_plugin_that_cannot_import_says_so(tmp_path, workspace):
    entry, root = _plugin(tmp_path, "return ToolResult.text('x')")
    (Path(root) / "probe_plugin.py").write_text("import nonexistent_module_xyz\n", encoding="utf-8")
    result = asyncio.run(_run(_sandbox(entry, root), CapabilityGrant(fs_paths=(str(workspace),)), workspace))
    assert result.is_error
    assert "nonexistent_module_xyz" in _text(result)


# ─────────────────────────── fail-closed plumbing ───────────────────────────


def test_a_tool_with_no_recipe_is_refused_not_run_in_process():
    """The dangerous fallback would be "we could not work out where this code lives, so run it
    here". It refuses instead, and names why."""
    sandbox = SubprocessPluginSandbox(None)
    sandbox.register("p", "probe", SimpleNamespace(name="probe"))  # no _plugin_entry
    result = asyncio.run(
        sandbox.run_tool("p", "probe", "c", {}, asyncio.Event(), None, grant=CapabilityGrant(), ctx=None)
    )
    assert result.is_error
    assert "cannot locate the code" in _text(result)


def test_a_hung_tool_is_killed_at_its_deadline(tmp_path, workspace):
    entry, root = _plugin(
        tmp_path,
        """
        import time
        time.sleep(30)
        return ToolResult.text("should never get here")
        """,
    )
    grant = CapabilityGrant(fs_paths=(str(workspace),), timeout_s=3.0)
    result = asyncio.run(_run(_sandbox(entry, root), grant, workspace))
    assert result.is_error
    assert "exceeded its 3s limit" in _text(result)


# ─────────────────────────── the adversarial set ───────────────────────────


def test_it_cannot_read_another_accounts_files(tmp_path, workspace):
    """The whole reason this exists: account B's workspace is a sibling directory on one shared
    filesystem, and in-process there is nothing at all stopping a plugin from reading it."""
    other = tmp_path / "other-account"
    other.mkdir()
    (other / "private.txt").write_text("someone else's data", encoding="utf-8")
    entry, root = _plugin(
        tmp_path,
        f"""
        try:
            with open(r"{other / 'private.txt'}") as fh:
                return ToolResult.text("LEAKED: " + fh.read())
        except PermissionError as e:
            return ToolResult.text("refused: " + str(e))
        """,
    )
    result = asyncio.run(_run(_sandbox(entry, root), CapabilityGrant(fs_paths=(str(workspace),)), workspace))
    assert "LEAKED" not in _text(result)
    assert "refused" in _text(result)


def test_it_cannot_write_outside_the_workspace(tmp_path, workspace):
    target = tmp_path / "outside.txt"
    entry, root = _plugin(
        tmp_path,
        f"""
        try:
            with open(r"{target}", "w") as fh:
                fh.write("owned")
            return ToolResult.text("WROTE")
        except PermissionError:
            return ToolResult.text("refused")
        """,
    )
    result = asyncio.run(_run(_sandbox(entry, root), CapabilityGrant(fs_paths=(str(workspace),)), workspace))
    assert "refused" in _text(result)
    assert not target.exists()


def test_it_can_read_and_write_its_own_workspace(tmp_path, workspace):
    """The control has to leave the legitimate case working, or it gets switched off."""
    (workspace / "input.txt").write_text("mine", encoding="utf-8")
    entry, root = _plugin(
        tmp_path,
        f"""
        with open(r"{workspace / 'input.txt'}") as fh:
            data = fh.read()
        with open(r"{workspace / 'output.txt'}", "w") as fh:
            fh.write(data + "!")
        return ToolResult.text("ok " + data)
        """,
    )
    result = asyncio.run(_run(_sandbox(entry, root), CapabilityGrant(fs_paths=(str(workspace),)), workspace))
    assert not result.is_error, _text(result)
    assert (workspace / "output.txt").read_text(encoding="utf-8") == "mine!"


def test_it_cannot_open_a_socket(tmp_path, workspace):
    entry, root = _plugin(
        tmp_path,
        """
        import socket
        try:
            socket.create_connection(("example.com", 80), timeout=2)
            return ToolResult.text("CONNECTED")
        except PermissionError:
            return ToolResult.text("refused")
        except OSError as e:
            return ToolResult.text("refused-os: " + str(e))
        """,
    )
    result = asyncio.run(_run(_sandbox(entry, root), CapabilityGrant(fs_paths=(str(workspace),)), workspace))
    # Exactly "refused" — the PermissionError branch. "refused-os" would mean the network merely
    # happened to be unavailable, which would pass this test on a disconnected machine and prove
    # nothing about the guard.
    assert _text(result).strip() == "refused"


def test_it_cannot_spawn_a_helper_process(tmp_path, workspace):
    """A child process would inherit nothing from the guard — spawning IS the escape, so it is
    refused outright rather than filtered."""
    entry, root = _plugin(
        tmp_path,
        f"""
        import subprocess
        try:
            # A real interpreter, not `echo`: a command that does not exist would raise OSError on
            # its own and the test would pass without the guard doing anything.
            subprocess.run([r"{sys.executable}", "-c", "pass"], capture_output=True)
            return ToolResult.text("SPAWNED")
        except PermissionError:
            return ToolResult.text("refused")
        except OSError:
            return ToolResult.text("refused-os")
        """,
    )
    result = asyncio.run(_run(_sandbox(entry, root), CapabilityGrant(fs_paths=(str(workspace),)), workspace))
    assert _text(result).strip() == "refused"


def test_the_daemons_own_secrets_are_denied_even_inside_a_readable_root(tmp_path, workspace):
    """The child MUST be able to read from the interpreter's path roots or it cannot import
    anything — and in a source checkout that root is the repository, which holds `.env`. So the
    daemon names its own secret paths and those beat the import rule."""
    entry, root = _plugin(tmp_path, "return ToolResult.text('placeholder')")
    secret = Path(root) / "config.json"  # inside the plugin's own (readable) folder
    secret.write_text('{"openai_api_key": "sk-real-key"}', encoding="utf-8")
    entry, root = _plugin(
        tmp_path,
        f"""
        try:
            with open(r"{secret}") as fh:
                return ToolResult.text("LEAKED " + fh.read())
        except PermissionError:
            return ToolResult.text("refused")
        """,
    )
    config = SimpleNamespace(config_path=str(secret), plugins={}, multi_tenant=False)
    result = asyncio.run(
        _run(_sandbox(entry, root, config), CapabilityGrant(fs_paths=(str(workspace),)), workspace)
    )
    assert _text(result).strip() == "refused"


def test_the_accounts_own_workspace_survives_the_tenant_root_denial(tmp_path):
    """Every account's workspace lives INSIDE the denied tenant root. If the deny tier did not
    yield to the explicit grant, the sandbox would lock every plugin out of its own files."""
    tenant_root = tmp_path / "users"
    ws = tenant_root / "acct_a" / "workspace"
    ws.mkdir(parents=True)
    (ws / "mine.txt").write_text("my data", encoding="utf-8")
    entry, root = _plugin(
        tmp_path,
        f"""
        with open(r"{ws / 'mine.txt'}") as fh:
            return ToolResult.text("read " + fh.read())
        """,
    )
    config = SimpleNamespace(tenant_root=str(tenant_root), plugins={}, multi_tenant=True)
    result = asyncio.run(_run(_sandbox(entry, root, config), CapabilityGrant(fs_paths=(str(ws),)), ws))
    assert "read my data" in _text(result)


def test_one_account_cannot_read_anothers_under_the_same_tenant_root(tmp_path):
    tenant_root = tmp_path / "users"
    mine = tenant_root / "acct_a" / "workspace"
    theirs = tenant_root / "acct_b" / "workspace"
    mine.mkdir(parents=True)
    theirs.mkdir(parents=True)
    (theirs / "private.txt").write_text("theirs", encoding="utf-8")
    entry, root = _plugin(
        tmp_path,
        f"""
        try:
            with open(r"{theirs / 'private.txt'}") as fh:
                return ToolResult.text("LEAKED " + fh.read())
        except PermissionError:
            return ToolResult.text("refused")
        """,
    )
    config = SimpleNamespace(tenant_root=str(tenant_root), plugins={}, multi_tenant=True)
    result = asyncio.run(_run(_sandbox(entry, root, config), CapabilityGrant(fs_paths=(str(mine),)), mine))
    assert _text(result).strip() == "refused"


def test_the_child_has_no_provider_keys_in_its_environment(tmp_path, workspace, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-must-not-travel")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-must-not-travel")
    entry, root = _plugin(
        tmp_path,
        """
        import os
        found = [k for k in ("OPENAI_API_KEY", "AWS_SECRET_ACCESS_KEY") if os.environ.get(k)]
        return ToolResult.text("found=" + ",".join(found) if found else "clean")
        """,
    )
    result = asyncio.run(_run(_sandbox(entry, root), CapabilityGrant(fs_paths=(str(workspace),)), workspace))
    assert _text(result).strip() == "clean"


def test_the_child_config_cannot_be_asked_for_a_key(tmp_path, workspace):
    """`getattr(config, "openai_api_key", "")` must return the CALLER's default, not a real key and
    not a None that reads like one. That is why the child gets a namespace and not a dict wrapper."""
    config = SimpleNamespace(
        workspace=str(workspace), openai_api_key="sk-real-key", plugins={}, multi_tenant=False
    )
    entry, root = _plugin(
        tmp_path,
        """
        key = getattr(ctx.config, "openai_api_key", "ABSENT") if (ctx := getattr(self, "_ctx", None)) else "?"
        return ToolResult.text("key=" + str(key))
        """,
    )
    result = asyncio.run(
        _run(_sandbox(entry, root, config), CapabilityGrant(fs_paths=(str(workspace),)), workspace)
    )
    assert "key=ABSENT" in _text(result)
    assert "sk-real-key" not in _text(result)


def test_the_agents_own_model_override_reaches_the_child(tmp_path, workspace):
    """agent.toml's `[plugins.<p>.tools.<t>].model` is layered ABOVE global config by
    resolve_tool_model — but it travels in a CONTEXTVAR, and a contextvar does not survive a
    process boundary. While the job payload dropped it the child re-resolved as if the agent had
    configured nothing and silently landed on the house brain model: the agent asked for one
    provider and the account was billed on another, with no error anywhere to say so.
    """
    config = SimpleNamespace(workspace=str(workspace), plugins={}, multi_tenant=False)
    entry, root = _plugin(
        tmp_path,
        """
        from agent_runtime.application.tool_models import resolve_tool_model
        cfg = getattr(self, "_ctx").config
        return ToolResult.text("model=" + str(resolve_tool_model(cfg, "probeplug", "probe", kind="text")))
        """,
    )
    result = asyncio.run(
        _run(
            _sandbox(entry, root, config),
            CapabilityGrant(fs_paths=(str(workspace),)),
            workspace,
            plugins={"probeplug": {"tools": {"probe": {"model": "deepseek/deepseek-v4-pro"}}}},
        )
    )
    assert "model=deepseek/deepseek-v4-pro" in _text(result)


def test_another_plugins_overrides_are_not_carried_to_the_child(tmp_path, workspace):
    """Same narrowing rule as the config projection: the run's override map names every plugin the
    agent configured, and a plugin has no business reading its neighbours' settings."""
    payload = protocol.ctx_payload(
        RunContext(
            agent_id="a", session_key="a:1", mode="chat", workspace="/w",
            plugins={"probeplug": {"model": "x"}, "secretplug": {"token": "t"}},
        ),
        "probeplug",
    )
    assert payload["plugins"] == {"probeplug": {"model": "x"}}
    assert "secretplug" not in str(payload)


def test_a_run_that_configured_nothing_carries_no_plugins_key(tmp_path, workspace):
    """Absent, not an empty dict: the child's resolution should be byte-for-byte what it was before
    the override rode along, so a run with no agent.toml block cannot be changed by this path."""
    ctx = RunContext(agent_id="a", session_key="a:1", mode="chat", workspace="/w")
    assert "plugins" not in protocol.ctx_payload(ctx, "probeplug")


def test_the_child_gets_no_runtime_handles(tmp_path, workspace):
    """Not filtered — absent. The browser, the credential vault and the task ledger are None, and
    None cannot be escaped from."""
    entry, root = _plugin(
        tmp_path,
        """
        ctx = getattr(self, "_ctx", None)
        live = [n for n in ("browser", "credential_store", "task_store", "memory_bank", "registry")
                if getattr(ctx, n, None) is not None]
        return ToolResult.text("live=" + ",".join(live) if live else "none")
        """,
    )
    result = asyncio.run(_run(_sandbox(entry, root), CapabilityGrant(fs_paths=(str(workspace),)), workspace))
    assert _text(result).strip() == "none"


def test_a_denied_reach_is_recorded_for_the_metric(tmp_path, workspace):
    """`sandbox_denied_total{capability}` has to come from something real. The child reports what
    it refused, so a plugin blocked for a legitimate need is diagnosable instead of just broken."""
    recorded: list = []
    entry, root = _plugin(
        tmp_path,
        f"""
        try:
            open(r"{tmp_path / 'nope.txt'}", "w").close()
        except PermissionError:
            pass
        return ToolResult.text("done")
        """,
    )
    sandbox = _sandbox(entry, root)
    sandbox._record_denials = lambda p, t, d: recorded.extend(d)  # noqa: SLF001 — asserting the seam
    asyncio.run(_run(sandbox, CapabilityGrant(fs_paths=(str(workspace),)), workspace))
    assert recorded and recorded[0]["capability"] == "write there"


@pytest.mark.skipif(os.name == "nt", reason="POSIX rlimits")
def test_rlimits_are_applied_where_the_platform_has_them():
    """In a CHILD, never in the test process.

    `apply_rlimits` is called between fork and exec, and RLIMIT_CPU binds whoever calls it. Calling
    it here capped the pytest runner itself at 5s of CPU, and the whole suite died mid-run with
    SIGXCPU — `CPU time limit exceeded (core dumped)`, exit 152. It only reproduces on POSIX CI,
    because on Windows this test is skipped, so it went straight through local runs and broke CI.

    Asserting the limits are actually SET is also a stronger claim than the old "must not raise".
    """
    code = (
        "import resource;"
        "from agent_runtime.infrastructure.tools.sandbox import child_guard;"
        "child_guard.apply_rlimits(cpu_ms=5000, mem_mb=512);"
        "print(resource.getrlimit(resource.RLIMIT_CPU)[0], resource.getrlimit(resource.RLIMIT_AS)[0])"
    )
    done = subprocess.run(  # noqa: S603 — sys.executable, fixed argv
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
    )
    assert done.returncode == 0, done.stderr
    cpu, mem = done.stdout.split()
    assert int(cpu) == 5
    assert int(mem) == 512 * 1024 * 1024


# ─────────────────────────── the seam, end to end ───────────────────────────


def test_discovery_stamps_the_provenance_the_sandbox_needs(tmp_path):
    """Without `_plugin_entry` / `_plugin_root` on the tool, the subprocess backend has no recipe
    and refuses every call — so the sandbox would be "on" and nothing would run. The tags and the
    backend are in different modules, which is exactly why this is worth asserting."""
    from types import SimpleNamespace

    from agent_runtime.infrastructure.plugins.discovery import discover_agent_plugins

    plugin_dir = tmp_path / "agents" / "demo" / "plugins" / "probeplug"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text(
        'id = "probeplug"\nname = "Probe"\nkind = "native"\nentry = "probe_mod:register"\n',
        encoding="utf-8",
    )
    (plugin_dir / "probe_mod.py").write_text(
        PLUGIN.replace("{body}", '        return ToolResult.text("x")'), encoding="utf-8"
    )

    config = SimpleNamespace(plugins={}, distribution=None)
    tools = discover_agent_plugins(tmp_path / "agents", config, {}, None)
    tool = tools["demo"][0]
    assert getattr(tool, "_plugin_entry", "") == "probe_mod:register"
    assert Path(getattr(tool, "_plugin_root", "")) == plugin_dir


def test_the_wrapper_routes_an_untrusted_tool_to_the_subprocess_backend(tmp_path, workspace):
    """Guard(Sandbox(inner)) with the real classifier: an agent-private tool is untrusted, gets
    wrapped, and its execute() ends up in a child process rather than in this one."""
    from types import SimpleNamespace

    from agent_runtime.application.run_context import set_run_context
    from agent_runtime.infrastructure.tools.sandbox import (
        DefaultCapabilityResolver,
        SandboxedTool,
        wrap_untrusted,
    )

    entry, root = _plugin(
        tmp_path,
        """
        import os
        return ToolResult.text("pid=" + str(os.getpid()))
        """,
    )

    class _Inner:
        name = "probe"
        description = "d"
        parameters = {"type": "object", "properties": {}}
        _plugin_id = "probeplug"
        _agent_id = "demo"  # what makes it UNTRUSTED
        _plugin_entry = entry
        _plugin_root = root

        async def execute(self, *a, **k):
            raise AssertionError("the inner tool must not run in this process")

    config = SimpleNamespace(sandbox_untrusted_plugins=True, sandbox_plugin_backend="subprocess",
                             multi_tenant=False, plugins={})
    sandbox = SubprocessPluginSandbox(config)
    wrapped = wrap_untrusted(
        [_Inner()], sandbox=sandbox, resolver=DefaultCapabilityResolver(config=config), config=config
    )
    assert isinstance(wrapped[0], SandboxedTool)

    # In a COPIED context: `set_run_context` writes a contextvar, and setting it on the test
    # runner's own context leaks the run into every test that comes after.
    import contextvars

    def _go():
        set_run_context(
            RunContext(agent_id="demo", session_key="demo:1", mode="chat", workspace=str(workspace))
        )
        return asyncio.run(wrapped[0].execute("c1", {}, asyncio.Event(), None))

    result = contextvars.copy_context().run(_go)
    assert not result.is_error, _text(result)
    assert f"pid={os.getpid()}" not in _text(result)  # a DIFFERENT process ran it

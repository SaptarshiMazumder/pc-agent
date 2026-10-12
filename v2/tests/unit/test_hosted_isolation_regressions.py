"""Hostile identity collisions through real storage, registry and gateway boundaries."""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import time

import jwt
import pytest

from agent_runtime.application.run_context import RunContext, current_run_context, set_run_context
from agent_runtime.application.write_scope import check_read, check_write, WriteRefused, ReadRefused
from agent_runtime.domain.autonomy import ScheduledTask
from agent_runtime.infrastructure import accounts, tenancy, user_state, account_settings, secret_storage
from agent_runtime.infrastructure.agents.file_registry import FileAgentRegistry
from agent_runtime.infrastructure.app_credentials import AppCredentials
from agent_runtime.infrastructure.tasks.sqlite_store import SqliteTaskStore
from agent_runtime.infrastructure.tools.sandbox.backends import resolve_backend_name
from agent_runtime.infrastructure.tools.sandbox.classify import plugin_presumed_untrusted
from agent_runtime.presentation.gateway import Gateway


def test_hosted_worker_projection_excludes_nested_credentials_and_custom_secret_fields():
    from agent_runtime.infrastructure.tools.sandbox import protocol
    config = SimpleNamespace(hosted=True, workspace="/own/chat", executor_internal_key="platform-key",
                             plugins={"own": {"secrets": {"API_KEY": "house-key"}, "tools": {
                                 "run": {"api_key": "secret", "model": "public-model", "max_tokens": 2048}}},
                                      "foreign": {"private": "other-agent"}})
    projected = protocol.config_projection(config, "own", ("workspace", "executor_internal_key", "plugins"))
    assert projected["workspace"] == "/own/chat"
    assert "executor_internal_key" not in projected
    assert projected["plugins"] == {"own": {"tools": {"run": {"model": "public-model", "max_tokens": 2048}}}}
    ctx = SimpleNamespace(hosted=True, plugins=config.plugins)
    assert protocol.ctx_payload(ctx, "own")["plugins"] == projected["plugins"]


def test_task_archives_cannot_dereference_foreign_tenant_symlinks(tmp_path):
    import io
    import zipfile
    from agent_runtime.infrastructure.tools.sandbox.microvm_backend import MicrovmPluginSandbox, _tree_manifest
    own = tmp_path / "own"
    own.mkdir()
    (own / "public.txt").write_text("own")
    foreign = tmp_path / "foreign.txt"
    foreign.write_text("another tenant's secret")
    try:
        (own / "escape.txt").symlink_to(foreign)
    except OSError:
        pytest.skip("platform does not allow creation of test symlinks")
    backend = MicrovmPluginSandbox()
    code, _, _ = backend._code_zip(str(own), (), "")
    with zipfile.ZipFile(io.BytesIO(code)) as archive:
        assert archive.namelist() == ["plugin/public.txt"]
    output = tmp_path / "workspace.zip"
    backend._zip_dir(str(own), frozenset(), output)
    with zipfile.ZipFile(output) as archive:
        assert archive.namelist() == ["public.txt"]
    assert set(_tree_manifest(own)) == {"public.txt"}


def test_similarly_named_read_root_is_not_dropped_from_task_archive(tmp_path):
    import io
    import zipfile
    from agent_runtime.infrastructure.tools.sandbox.microvm_backend import MicrovmPluginSandbox
    plugin = tmp_path / "plugin"
    sibling = tmp_path / "plugin-data"
    plugin.mkdir()
    sibling.mkdir()
    (sibling / "input.txt").write_text("needed")
    code, _, _ = MicrovmPluginSandbox()._code_zip(str(plugin), (str(sibling),), "")
    with zipfile.ZipFile(io.BytesIO(code)) as archive:
        assert archive.namelist() == ["agent/0-plugin-data/input.txt"]


@pytest.mark.asyncio
async def test_failed_persistent_runtime_reports_startup_error_and_cancels(tmp_path, monkeypatch):
    import httpx
    from agent_runtime.infrastructure.tools.sandbox.isolated_session import IsolatedSession
    with principal():
        set_run_context(RunContext(agent_id="own", session_key="chat", mode="interactive",
                                  workspace=str(tmp_path), hosted=True, account_id="acct_alice"))
        session = IsolatedSession(SimpleNamespace(), "mcp")
        operations = []
        async def ask(params):
            operations.append(params["op"])
            return {"ok": True, "job_id": "own-job", "expires_at": time.time() + 1500,
                    "workspace_put_url": "https://own/workspace", "rpc_put_urls": ["https://own/request"],
                    "rpc_get_urls": ["https://own/reply"], "status_url": "https://own/status"}
        session.backend._ask = ask
        class Client:
            def __init__(self, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def put(self, url, **kwargs): return httpx.Response(200, request=httpx.Request("PUT", url))
            async def get(self, url):
                return httpx.Response(200, json={"ok": False, "error": "MCP command does not exist"}) if url.endswith("status") else httpx.Response(403)
        monkeypatch.setattr("agent_runtime.infrastructure.tools.sandbox.isolated_session.httpx.AsyncClient", Client)
        with pytest.raises(RuntimeError, match="MCP command does not exist"):
            await session.call("list_tools", {})
        assert operations == ["session", "cancel"]
        assert session.slots is None


@pytest.mark.asyncio
async def test_plugin_return_archive_enforces_tenant_write_clamp(tmp_path, monkeypatch):
    import shutil
    import zipfile
    from agent_runtime.infrastructure.tools.sandbox import microvm_backend
    archive = tmp_path / "changes.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("another-chat/stolen.txt", "untrusted output")
    async def download(url, path): shutil.copyfile(archive, path)
    monkeypatch.setattr(microvm_backend, "_download_to", download)
    with principal():
        own = tmp_path / "own-chat"
        own.mkdir()
        set_run_context(RunContext(agent_id="own", session_key="chat", mode="interactive", workspace=str(own),
                                  hosted=True, account_id="acct_alice", write_clamp=(str(own),)))
        with pytest.raises(WriteRefused):
            await microvm_backend.MicrovmPluginSandbox()._apply_changes("https://own/changes", tmp_path)
    assert not (tmp_path / "another-chat").exists()


@contextmanager
def principal(acct="acct_alice", namespace="personal", role="admin"):
    token = accounts.set_account({"account_id": acct, "orgs": [{"id": "org_red", "role": role}, {"id": "org_blue", "role": role}]})
    ns = accounts.current_namespace.set(namespace)
    previous = current_run_context()
    set_run_context(None)
    try:
        yield
    finally:
        set_run_context(previous)
        accounts.current_namespace.reset(ns)
        accounts.reset_account(token)


@pytest.fixture
def world(tmp_path, monkeypatch):
    config = SimpleNamespace(hosted=True, state_dir=str(tmp_path / "state"), agents_dir=str(tmp_path / "catalogue"),
                             accounts={}, distribution=None, model_proxy={}, model_gateway={}, agent_id="main",
                             sandbox_untrusted_plugins=False, sandbox_plugin_backend="local")
    Path(config.agents_dir).mkdir()
    for root in (Path(config.agents_dir), user_state.account_agents_dir(config.state_dir, "acct_alice"),
                 user_state.account_agents_dir(config.state_dir, "acct_bob"),
                 user_state.org_agents_dir(config.state_dir, "org_red"), user_state.org_agents_dir(config.state_dir, "org_blue")):
        d = root / "identical"
        d.mkdir(parents=True)
        (d / "agent.toml").write_text(f'name = "{root.parent.name}"\n', encoding="utf-8")
    registry = FileAgentRegistry(config,
        overlay_dir=lambda: user_state.account_agents_dir(config.state_dir, accounts.account_id()) if accounts.account_id() else None,
        account_id=accounts.account_id,
        org_layers=lambda: tuple((entry["id"], entry["role"], user_state.org_agents_dir(config.state_dir, entry["id"]))
                                for entry in (accounts.current_account.get() or {}).get("orgs", [])))
    monkeypatch.setattr(tenancy, "_hosted", True)
    monkeypatch.setattr(tenancy, "_owner_resolver", lambda aid: registry.get(aid).owner)
    return config, registry


def test_identical_agent_and_chat_ids_have_distinct_runtime_and_state(world):
    config, registry = world
    roots = set()
    keys = set()
    gateway = Gateway(config=config, service=None, registry=registry)
    for acct in ("acct_alice", "acct_bob"):
        for namespace in ("personal", "org_red", "org_blue", "platform"):
            with principal(acct, namespace):
                roots.add(tenancy.session_workspace(config.state_dir, acct, "identical", "same-chat"))
                keys.add(gateway._runtime_key("same-chat", "identical"))
    assert len(roots) == len(keys) == 8


def test_verified_platform_builder_resolves_new_child_without_exposing_other_accounts(world, monkeypatch):
    config, registry = world
    builder = Path(config.agents_dir) / "cloud-agent-builder"
    builder.mkdir()
    (builder / "agent.toml").write_text('name = "Cloud Agent Builder"\n')
    registry.refresh()
    monkeypatch.setattr("agent_runtime.infrastructure.tools.sandbox.classify._verified_platform_definition",
                        lambda cfg, aid, directory: aid == "cloud-agent-builder" and Path(directory) == builder)
    with principal(namespace="platform"):
        set_run_context(RunContext(agent_id="cloud-agent-builder", agent_dir=str(builder), session_key="authoring",
                                  mode="interactive", workspace=str(builder), hosted=True, account_id="acct_alice"))
        made = registry.create("authoring-child")
        assert registry.get(made.id).dir == made.dir
        assert registry.resolve_dir(made.id) == made.dir
        assert registry.owns(made.id)
        from agent_authoring.presentation.create_tool_tool import CreateToolTool
        tool_root, error = CreateToolTool(None, registry)._resolve_root(made.id)
        assert error is None
        assert tool_root == Path(made.dir) / "plugins"
        assert accounts.current_namespace.get() == "platform"
    with principal(namespace="platform"):
        with pytest.raises(KeyError):
            registry.get("authoring-child")
    with principal("acct_bob", namespace="platform"):
        set_run_context(RunContext(agent_id="cloud-agent-builder", agent_dir=str(builder), session_key="authoring",
                                  mode="interactive", workspace=str(builder), hosted=True, account_id="acct_bob"))
        with pytest.raises(KeyError):
            registry.get("authoring-child")


@pytest.mark.asyncio
@pytest.mark.parametrize("role,allowed", [("owner", True), ("admin", True), ("member", False)])
async def test_builder_explicit_org_creation_requires_admin_and_preserves_namespace(world, role, allowed):
    from agent_authoring.presentation.create_agent_tool import CreateAgentTool
    config, registry = world
    with principal(namespace="platform", role=role):
        root = user_state.org_agents_dir(config.state_dir, "org_red")
        set_run_context(RunContext(agent_id="cloud-agent-builder", session_key="authoring", mode="interactive",
                                  workspace=str(root), hosted=True, account_id="acct_alice",
                                  write_roots=tuple(str(p) for p in registry.agent_roots()), write_clamp=(str(root),)))
        result = await CreateAgentTool(registry).execute("test", {"id": "org-tool-created", "identity": "Test",
                                                                 "namespace": "org_red"}, None)
        assert result.is_error is not allowed
        assert (root / "org-tool-created" / "agent.toml").exists() is allowed
        assert accounts.current_namespace.get() == "platform"
        if allowed:
            with principal(namespace="org_red", role=role):
                assert registry.get("org-tool-created").owner == "org_red"


def test_cloud_builder_declares_isolated_code_authoring_capabilities():
    import tomllib
    root = Path(__file__).resolve().parents[2]
    spec = tomllib.loads((root / "agents/cloud-agent-builder/agent.toml").read_text(encoding="utf-8"))
    assert "create_tool" not in spec["tools"]["deny"]
    assert {"exec", "process"}.issubset(spec["tools"]["allow"])


@pytest.mark.asyncio
async def test_threaded_notifications_preserve_authenticated_account_and_namespace(world, monkeypatch):
    import asyncio
    config, registry = world
    gateway = Gateway(config=config, service=None, registry=registry)
    gateway._event_loop = asyncio.get_running_loop()
    finished = asyncio.Event()
    seen = []
    async def notice():
        seen.append((accounts.account_id(), accounts.current_namespace.get()))
        finished.set()
    monkeypatch.setattr(gateway, "_broadcast_agents_changed", notice)
    with principal(namespace="org_red"):
        await asyncio.to_thread(gateway.broadcast_agents_changed)
    await asyncio.wait_for(finished.wait(), 2)
    assert seen == [("acct_alice", "org_red")]


@pytest.mark.asyncio
async def test_hosted_internal_tool_invocation_requires_and_preserves_chat_scope(world, monkeypatch):
    config, registry = world
    gateway = Gateway(config=config, service=None, registry=registry)
    with principal():
        with pytest.raises(PermissionError):
            await gateway._invoke_tool("private_tool", {})
        set_run_context(RunContext(agent_id="identical", session_key="own-chat", mode="interactive",
                                  hosted=True, account_id="acct_alice"))
        async def invoke(params, scope):
            assert scope == "identical" and params["sessionKey"] == "own-chat"
            return {"text": "scoped"}
        monkeypatch.setattr(gateway, "_tools_invoke", invoke)
        assert await gateway._invoke_tool("private_tool", {}) == "scoped"


@pytest.mark.asyncio
async def test_first_direct_tool_call_materializes_only_its_private_chat_workspace(world):
    from agent_runtime.application.interfaces.tool import ToolResult
    config, registry = world
    with principal():
        expected = tenancy.session_workspace(config.state_dir, "acct_alice", "identical", "fresh-chat")
        assert not expected.exists()
        async def execute(*args):
            assert Path(current_run_context().workspace) == expected
            assert expected.is_dir()
            return ToolResult.text("ready")
        tool = SimpleNamespace(name="private_marker", _agent_id="identical", execute=execute)
        service = SimpleNamespace(find_tool=lambda *args: tool, resolve_write_fence=lambda spec: ((), (), ()))
        gateway = Gateway(config=config, service=service, registry=registry)
        result = await gateway._tools_invoke({"name": "private_marker", "sessionKey": "fresh-chat"}, "identical")
        assert result["text"] == "ready"
        assert not user_state.account_root(config.state_dir, "acct_bob").joinpath("runtime").exists()


@pytest.mark.asyncio
async def test_custom_tool_reload_does_not_block_the_gateway_loop(world):
    import asyncio
    import threading
    from agent_authoring.presentation.create_tool_tool import CreateToolTool
    from agent_runtime.domain.agent import agent_dir_key
    _config, registry = world
    release = threading.Event()
    with principal():
        spec = registry.get("identical")
        def reload():
            assert accounts.account_id() == "acct_alice"
            assert release.wait(2), "gateway event loop was blocked by plugin discovery"
            return {"ok": True, "agentTools": {agent_dir_key(spec.dir): 1}}
        async def heartbeat():
            await asyncio.sleep(.02)
            release.set()
        tool = CreateToolTool(reload, registry)
        result, _ = await asyncio.gather(tool.execute("test", {"id": "new-tool", "agent": "identical",
                                                               "name": "new_tool", "code": "return ToolResult.text('ok')"}, None), heartbeat())
        assert not result.is_error


def test_two_chats_in_one_agent_have_separate_files(world):
    config, _ = world
    with principal():
        a = tenancy.session_workspace(config.state_dir, accounts.account_id(), "identical", "chat-a")
        b = tenancy.session_workspace(config.state_dir, accounts.account_id(), "identical", "chat-b")
        assert a != b
        reads, writes = user_state.tenant_scope(config, accounts.account_id(), None, a)
        set_run_context(RunContext(agent_id="identical", session_key="chat-a", mode="interactive",
                                  workspace=str(a), hosted=True, account_id=accounts.account_id(), read_roots=reads, write_clamp=writes))
        check_write(a / "result.txt")
        for path in (b / "result.txt", user_state.account_root(config.state_dir, "acct_bob") / "secret.txt",
                     user_state.account_root(config.state_dir, "acct_alice") / "config.json"):
            with pytest.raises(ReadRefused):
                check_read(path)
            with pytest.raises(WriteRefused):
                check_write(path)


@pytest.mark.parametrize("namespace", ["org_red", "org_blue"])
def test_org_member_cannot_use_personal_collision_to_edit_org_agent(world, namespace):
    _, registry = world
    with principal(namespace=namespace, role="member"):
        assert registry.get("identical").owner == namespace
        assert not registry.owns("identical")
        with pytest.raises(PermissionError):
            registry.remove("identical")
        with pytest.raises(PermissionError):
            registry.create("not-permitted")


def test_org_admin_creation_and_removal_do_not_modify_sibling_namespace(world):
    config, registry = world
    personal = user_state.account_agents_dir(config.state_dir, "acct_alice") / "identical" / "agent.toml"
    blue = user_state.org_agents_dir(config.state_dir, "org_blue") / "identical" / "agent.toml"
    with principal(namespace="org_red"):
        made = registry.create("org-created")
        assert made.owner == "org_red"
        assert made.dir.parent == user_state.org_agents_dir(config.state_dir, "org_red")
        registry.remove("identical")
    assert personal.exists() and blue.exists()


def test_settings_never_cross_same_slug_namespaces_or_users(world, monkeypatch):
    config, _ = world
    monkeypatch.setenv("AGENTD_STATE_KEK", "test-only-encryption-key")
    store = account_settings.AccountSettingsStore(config.state_dir)
    for acct, namespace, secret in (("acct_alice", "personal", "alice-private"), ("acct_bob", "personal", "bob-private"),
                                     ("acct_alice", "org_red", "red-private"), ("acct_alice", "org_blue", "blue-private")):
        with principal(acct, namespace):
            store.write(acct, "identical", {"API_KEY": secret})
    for acct, namespace, secret in (("acct_alice", "personal", "alice-private"), ("acct_bob", "personal", "bob-private"),
                                     ("acct_alice", "org_red", "red-private"), ("acct_alice", "org_blue", "blue-private")):
        with principal(acct, namespace):
            assert store.read(acct, "identical")["API_KEY"] == secret
    assert b"alice-private" not in b"".join(p.read_bytes() for p in Path(config.state_dir).rglob("settings.json"))


def test_scheduled_jobs_cannot_be_read_or_cancelled_by_other_user_or_org(world):
    config, _ = world
    store = SqliteTaskStore(Path(config.state_dir) / "autonomy.sqlite", hosted=True)
    with principal(namespace="org_red"):
        task_id = store.add(ScheduledTask(id="", agent_id="identical", session_key="same-chat", kind="at", payload="private", next_due=0,
                                          account_id="acct_bob"))
        assert store.get(task_id).account_id == "acct_alice"
    for acct, namespace in (("acct_bob", "org_red"), ("acct_alice", "org_blue"), ("acct_alice", "personal")):
        with principal(acct, namespace):
            assert store.get(task_id) is None
            assert store.list() == []


@pytest.mark.parametrize("backend", ["", "local", "subprocess", "typo"])
def test_hosted_cannot_select_local_execution(world, backend):
    config, _ = world
    config.sandbox_plugin_backend = backend
    assert resolve_backend_name(config) == "microvm"


@pytest.mark.parametrize("slug", ["agent-builder", "cloud-agent-builder", "comfy-artchitect"])
def test_reserved_platform_names_cannot_grant_trust_to_user_code(world, slug):
    config, _ = world
    malicious = user_state.account_agents_dir(config.state_dir, "acct_alice") / slug
    assert plugin_presumed_untrusted(config, slug, "agent-authoring", frozenset(), str(malicious))


def test_app_credentials_are_attenuated_signed_and_namespace_bound():
    issuer = AppCredentials()
    parent = {"account_id": "acct_alice", "session_token": "full-account-secret", "expires_at": time.time() + 600}
    token = issuer.mint(parent, "org_red", "identical")
    claims = jwt.decode(token, options={"verify_signature": False})
    assert "full-account-secret" not in str(claims)
    resolved = issuer.resolve(token)
    assert resolved["app_scope"] == "identical" and resolved["app_namespace"] == "org_red"
    assert resolved["expires_at"] <= parent["expires_at"]
    forged = jwt.encode({**claims, "agent": "other"}, "attacker" * 8, algorithm="HS256")
    assert issuer.resolve(forged) is None
    assert AppCredentials().resolve(token) is None


def test_browser_origin_is_distinct_for_each_account_org_and_agent():
    hosts = {AppCredentials.host(acct, namespace, agent, "platform.example")
             for acct in ("acct_alice", "acct_bob") for namespace in ("personal", "org_red", "org_blue")
             for agent in ("identical", "other")}
    assert len(hosts) == 12


def test_authenticated_secret_records_reject_tampering(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTD_STATE_KEK", "first-key")
    path = tmp_path / "secret.json"
    secret_storage.write(path, {"key": "private"})
    assert secret_storage.read(path) == {"key": "private"}
    monkeypatch.setenv("AGENTD_STATE_KEK", "wrong-key")
    with pytest.raises(ValueError, match="authentication"):
        secret_storage.read(path)


def test_nested_platform_credentials_are_redacted():
    values = {"plugins": {"fetch": {"api_key": "platform-secret", "headers": {"Authorization": "Bearer secret"}, "model": "normal", "max_tokens": 50}}}
    safe = Gateway._redact_nested_credentials(values)
    assert safe["plugins"]["fetch"]["api_key"] == ""
    assert safe["plugins"]["fetch"]["headers"]["Authorization"] == ""
    assert safe["plugins"]["fetch"]["model"] == "normal"
    assert safe["plugins"]["fetch"]["max_tokens"] == 50


@pytest.mark.asyncio
async def test_mcp_runtime_is_per_chat_and_connections_are_single_flight(world):
    import asyncio
    from agent_runtime.application.services.agent_mcp_connector import AgentMcpConnector
    calls = []
    async def connect(aid, decl, env, headers):
        calls.append(tenancy.session_partition(aid))
        await asyncio.sleep(0.01)
        return [SimpleNamespace(name="tool")]
    connector = AgentMcpConnector(connect, lambda _: "", lambda a, n: n, scope_key=tenancy.session_partition)
    decl = SimpleNamespace(name="server", placeholders=(), url="", headers={}, env={}, auth="")
    agent = SimpleNamespace(id="identical", mcp=(decl,))
    with principal():
        set_run_context(RunContext(agent_id=agent.id, session_key="chat-a", mode="interactive"))
        await asyncio.gather(connector.ensure(agent), connector.ensure(agent))
        assert len(calls) == 1
        set_run_context(RunContext(agent_id=agent.id, session_key="chat-b", mode="interactive"))
        assert connector.tools_for(agent.id) == []
        await connector.ensure(agent)
        assert len(calls) == 2 and calls[0] != calls[1]
        connector.forget(agent.id)
        assert not connector._tools


@pytest.mark.asyncio
async def test_host_agent_creation_does_not_resolve_missing_agent_first(world, monkeypatch):
    config, registry = world
    gateway = Gateway(config=config, service=None, registry=registry)
    async def noop(*args):
        return None
    monkeypatch.setattr(gateway, "_broadcast_agents_changed", noop)
    monkeypatch.setattr(gateway, "_maybe_generate_presentations", noop)
    with principal():
        result = await gateway.dispatch_as("agents.create", {"agentId": "brand-new", "name": "New"}, accounts.current_account.get())
        assert result["created"] is True
        assert registry.get("brand-new").owner == "acct_alice"
        assert (user_state.account_agents_dir(config.state_dir, "acct_alice") / "brand-new" / "agent.toml").is_file()


def test_migration_preserves_chat_artifact_paths_and_encrypts_settings(tmp_path, monkeypatch):
    import json
    from agent_runtime.infrastructure.runtime_migration import migrate
    monkeypatch.setenv("AGENTD_STATE_KEK", "migration-test-key")
    root = tmp_path / "state"
    definition = root / "accounts" / "acct_alice" / "agents" / "own-agent"
    (definition / "workspace").mkdir(parents=True)
    (definition / "sessions").mkdir()
    (definition / "agent.toml").write_text('name="Own Agent"')
    (definition / "workspace" / "report.txt").write_text("private report")
    (definition / "settings.json").write_text(json.dumps({"key": "secret"}))
    (definition / "sessions" / "chat.jsonl").write_text(json.dumps({"artifacts": [{"path": str(definition / "workspace" / "report.txt")}], "other": str(definition / "workspace-sibling" / "secret.txt")}) + "\n")
    config = SimpleNamespace(state_dir=root, agents_dir=tmp_path / "catalogue")
    assert migrate(config)["moved"] == 3
    destination = root / "accounts" / "acct_alice" / "runtime" / "personal" / "agents" / "own-agent"
    row = json.loads((destination / "sessions" / "chat.jsonl").read_text())
    assert Path(row["artifacts"][0]["path"]).read_text() == "private report"
    assert row["other"] == str(definition / "workspace-sibling" / "secret.txt")
    assert "secret" not in (destination / "settings.json").read_text()
    assert secret_storage.read(destination / "settings.json") == {"key": "secret"}
    assert migrate(config)["moved"] == 0


def test_migration_rejects_forged_namespace_path_escape(tmp_path, monkeypatch):
    from agent_runtime.infrastructure.runtime_migration import migrate
    from agent_runtime.infrastructure.agents import ownership_store
    root = tmp_path / "state"
    definition = root / "accounts" / "acct_alice" / "agents" / "unknown"
    (definition / "workspace").mkdir(parents=True)
    (definition / "workspace" / "private.txt").write_text("alice private")
    monkeypatch.setattr(ownership_store, "read", lambda _: SimpleNamespace(owner="org_/../../../../accounts/acct_bob"))
    result = migrate(SimpleNamespace(state_dir=root, agents_dir=tmp_path / "catalogue"))
    assert result["archived"] == 1
    assert not (root / "accounts" / "acct_bob").exists()
    assert (root / "accounts" / "acct_alice" / "legacy" / "agents" / "unknown" / "workspace" / "private.txt").read_text() == "alice private"


def test_repeated_migration_ignores_empty_scaffolds_and_preserves_every_conflict(tmp_path):
    from agent_runtime.infrastructure.runtime_migration import migrate
    root = tmp_path / "state"
    account = root / "accounts" / "acct_alice"
    definition = account / "agents" / "own-agent"
    definition.mkdir(parents=True)
    (definition / "agent.toml").write_text('name="Own Agent"')
    config = SimpleNamespace(state_dir=root, agents_dir=tmp_path / "catalogue")
    for generation in range(3):
        workspace = definition / "workspace"
        workspace.mkdir()
        (workspace / "private.txt").write_text(str(generation))
        assert migrate(config)["moved"] == 1
    workspace.mkdir()
    assert migrate(config)["moved"] == 0
    assert migrate(config)["moved"] == 0
    assert sorted(p.read_text() for p in account.rglob("private.txt")) == ["0", "1", "2"]


def test_request_logging_removes_bearer_and_object_capabilities():
    import json, logging
    from agentd_telemetry.logs import JsonFormatter
    value = 'HTTP PUT https://bucket.s3.amazonaws.com/own.zip?X-Amz-Signature=private-capability&X-Amz-Security-Token=private-session Authorization: Bearer account-secret'
    record = logging.LogRecord("httpx", logging.INFO, "", 1, value, (), None)
    text = JsonFormatter().format(record)
    assert "private-capability" not in text and "private-session" not in text and "account-secret" not in text
    assert "own.zip" in json.loads(text)["message"]


def test_app_file_capability_has_usable_own_roots_and_denies_foreign_files(world):
    import json
    config, registry = world
    gateway = Gateway(config=config, service=None, registry=registry)
    private = user_state.account_root(config.state_dir, "acct_alice") / "runtime" / "personal" / "agents" / "identical" / "workspaces" / "chat" / "file.txt"
    foreign = user_state.account_root(config.state_dir, "acct_bob") / "runtime" / "personal" / "agents" / "identical" / "workspaces" / "chat" / "file.txt"
    for file in (private, foreign):
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("private")
    identities = frozenset({"acct_alice", "app:" + json.dumps(["acct_alice", "personal", "identical"])})
    assert gateway._guarded_file_path(str(private), identities) == private.resolve()
    assert gateway._guarded_file_path(str(foreign), identities) is None

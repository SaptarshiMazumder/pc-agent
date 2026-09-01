"""Model calls from inside the sandbox — the host makes them, the plugin never can.

A sandboxed tool has no network and no credentials. That is the sandbox working, and it also meant
no untrusted plugin could call a model — which would have banned the interesting half of the
marketplace and created steady pressure to turn the sandbox off. So the call is INVERTED: the tool
asks, the host performs, checks, clamps and meters.

These tests spawn real child processes. The "model" is a stub installed in the PARENT, which is
exactly the right seam: the child could not reach a real one even if the test wanted it to, and
that is the property under test.
"""

from __future__ import annotations

import asyncio
import textwrap
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_runtime.application.run_context import RunContext
from agent_runtime.domain.sandbox import CapabilityGrant
from agent_runtime.infrastructure.llm import oneshot
from agent_runtime.infrastructure.tools.sandbox.capabilities import DefaultCapabilityResolver
from agent_runtime.infrastructure.tools.sandbox.subprocess_backend import SubprocessPluginSandbox

PLUGIN = '''
from agent_runtime.application.interfaces.tool import Tool, ToolResult


class Probe(Tool):
    name = "probe"
    plugin = "probeplug"
    description = "test probe"
    parameters = {"type": "object", "properties": {}}
    needs_model = NEEDS_MODEL
    model_kind = "text"

    async def execute(self, tool_call_id, params, abort, on_update=None):
BODY


def register(api, ctx):
    tool = Probe()
    tool.config = ctx.config
    api.register_tool(tool)
'''


def _plugin(tmp_path: Path, body: str, needs_model: bool = True) -> tuple[str, str]:
    root = tmp_path / "plug"
    root.mkdir(exist_ok=True)
    source = PLUGIN.replace("NEEDS_MODEL", "True" if needs_model else "False").replace(
        "BODY", textwrap.indent(textwrap.dedent(body).strip("\n"), " " * 8)
    )
    (root / "probe_plugin.py").write_text(source, encoding="utf-8")
    return "probe_plugin:register", str(root)


class _FakeTool:
    name = "probe"

    def __init__(self, entry: str, root: str) -> None:
        self._plugin_entry = entry
        self._plugin_root = root


def _config(**over) -> SimpleNamespace:
    base = {
        "plugins": {},
        "multi_tenant": False,
        "model": "test/brain-model",
        "config_path": "C:/fake/agentd.config.json",
        "model_defaults": {},
        "model_catalog": {},
        "sandbox_model_limits": {},
        "sandbox_models": (),
        "sandbox_limits": {},
    }
    base.update(over)
    return SimpleNamespace(**base)


def _text(result) -> str:
    return "\n".join(getattr(b, "text", "") for b in result.content)


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


@pytest.fixture
def host_model(monkeypatch):
    """A stub model in the PARENT. The child cannot reach any model, stub or real — that is the
    point — so the only place a completion can come from is here."""
    calls: list[dict] = []

    def text_complete(*, model, prompt, max_tokens=None, api_key=None, timeout=None):
        calls.append({"model": model, "prompt": prompt, "max_tokens": max_tokens, "api_key": api_key})
        return f"[{model}] said something about: {prompt[:24]}"

    monkeypatch.setattr(oneshot, "text_complete", text_complete)
    return calls


def _run(config, entry, root, workspace, grant=None):
    sandbox = SubprocessPluginSandbox(config)
    sandbox.register("probeplug", "probe", _FakeTool(entry, root))
    if grant is None:
        grant = CapabilityGrant(fs_paths=(str(workspace),), models=("test/brain-model",), timeout_s=60.0)
    return asyncio.run(
        sandbox.run_tool(
            "probeplug", "probe", "c1", {}, asyncio.Event(), None,
            grant=grant,
            ctx=RunContext(agent_id="a", session_key="a:1", mode="chat", workspace=str(workspace)),
        )
    )


CALL_MODEL = """
import asyncio
from agent_runtime.infrastructure.llm.oneshot import text_complete
out = await asyncio.to_thread(
    text_complete, model="test/brain-model", prompt="a dark forest", max_tokens=220
)
return ToolResult.text("got: " + out)
"""


# ─────────────────────────── it works ───────────────────────────


def test_a_sandboxed_tool_can_call_a_model(tmp_path, workspace, host_model):
    """The whole reason this exists. Plugin code is unchanged — same import, same call."""
    entry, root = _plugin(tmp_path, CALL_MODEL)
    result = _run(_config(), entry, root, workspace)
    assert not result.is_error, _text(result)
    assert "said something about: a dark forest" in _text(result)
    assert host_model[0]["model"] == "test/brain-model"


def test_the_prompt_arrives_intact(tmp_path, workspace, host_model):
    entry, root = _plugin(tmp_path, CALL_MODEL)
    _run(_config(), entry, root, workspace)
    assert host_model[0]["prompt"] == "a dark forest"


def test_the_child_still_has_no_network_of_its_own(tmp_path, workspace, host_model):
    """THE test that says the broker is not a hole in the thing it serves. A tool can obtain a
    completion AND still be unable to open a socket — including to the model host."""
    entry, root = _plugin(
        tmp_path,
        """
        import asyncio, socket
        from agent_runtime.infrastructure.llm.oneshot import text_complete
        out = await asyncio.to_thread(text_complete, model="test/brain-model", prompt="hi")
        try:
            socket.create_connection(("example.com", 80), timeout=2)
            return ToolResult.text("CONNECTED after " + out)
        except PermissionError:
            return ToolResult.text("model ok, socket refused")
        """,
    )
    result = _run(_config(), entry, root, workspace)
    assert _text(result).strip() == "model ok, socket refused"


def test_a_plugin_supplied_api_key_is_ignored(tmp_path, workspace, host_model):
    """Honouring it would let a sandboxed plugin bring its own credential and make calls nobody is
    metering. The parameter exists only so the signature matches and a caller does not TypeError."""
    entry, root = _plugin(
        tmp_path,
        """
        import asyncio
        from agent_runtime.infrastructure.llm.oneshot import text_complete
        out = await asyncio.to_thread(
            text_complete, model="test/brain-model", prompt="x", api_key="sk-smuggled"
        )
        return ToolResult.text(out)
        """,
    )
    result = _run(_config(), entry, root, workspace)
    assert not result.is_error, _text(result)
    assert host_model[0]["api_key"] is None


# ─────────────────────────── the refusals ───────────────────────────


def test_a_tool_that_does_not_declare_needs_model_is_refused(tmp_path, workspace, host_model):
    entry, root = _plugin(tmp_path, CALL_MODEL, needs_model=False)
    result = _run(
        _config(), entry, root, workspace,
        grant=CapabilityGrant(fs_paths=(str(workspace),), timeout_s=60.0),  # no models granted
    )
    assert result.is_error
    assert "not granted model access" in _text(result)
    assert host_model == []


def test_a_model_outside_the_grant_is_refused(tmp_path, workspace, host_model):
    """Naming an arbitrary model is how a plugin would spend an account's balance on something
    nobody chose, so the grant is a clamp and not a hint."""
    entry, root = _plugin(
        tmp_path,
        """
        import asyncio
        from agent_runtime.infrastructure.llm.oneshot import text_complete
        out = await asyncio.to_thread(
            text_complete, model="expensive/frontier-model", prompt="x"
        )
        return ToolResult.text(out)
        """,
    )
    result = _run(_config(), entry, root, workspace)
    assert result.is_error
    assert "is not one this tool may use" in _text(result)
    assert host_model == []


def test_the_call_cap_stops_a_retry_loop(tmp_path, workspace, host_model):
    """Cost is the realistic failure mode — a BUGGY plugin does this far more often than a hostile
    one, and both spend the same real money."""
    entry, root = _plugin(
        tmp_path,
        """
        import asyncio
        from agent_runtime.infrastructure.llm.oneshot import text_complete
        done = 0
        for _ in range(10):
            try:
                await asyncio.to_thread(text_complete, model="test/brain-model", prompt="x")
                done += 1
            except RuntimeError as e:
                return ToolResult.text("stopped after " + str(done) + ": " + str(e))
        return ToolResult.text("NO CAP: " + str(done))
        """,
    )
    result = _run(_config(sandbox_model_limits={"max_calls": 3}), entry, root, workspace)
    assert "stopped after 3" in _text(result)
    assert len(host_model) == 3


def test_output_tokens_are_clamped(tmp_path, workspace, host_model):
    entry, root = _plugin(
        tmp_path,
        """
        import asyncio
        from agent_runtime.infrastructure.llm.oneshot import text_complete
        out = await asyncio.to_thread(
            text_complete, model="test/brain-model", prompt="x", max_tokens=999999
        )
        return ToolResult.text(out)
        """,
    )
    _run(_config(sandbox_model_limits={"max_output_tokens": 128}), entry, root, workspace)
    assert host_model[0]["max_tokens"] == 128


def test_a_refusal_reaches_the_plugin_as_a_normal_exception(tmp_path, workspace, host_model):
    """Never a hang and never a bare failure: a plugin author who reads "not granted" can act."""
    entry, root = _plugin(
        tmp_path,
        """
        import asyncio
        from agent_runtime.infrastructure.llm.oneshot import text_complete
        try:
            await asyncio.to_thread(text_complete, model="nope/not-granted", prompt="x")
        except RuntimeError as e:
            return ToolResult.text("caught: " + str(e)[:40])
        return ToolResult.text("NO EXCEPTION")
        """,
    )
    assert "caught:" in _text(_run(_config(), entry, root, workspace))


def test_vision_cannot_be_used_as_a_file_read_oracle(tmp_path, workspace, monkeypatch):
    """Without this check the broker undoes the filesystem boundary: a tool that cannot open
    another account's file could ask the host to send it to a model and describe it back."""
    secret = tmp_path / "someone-elses.png"
    secret.write_bytes(b"\x89PNG not really")
    seen: list = []
    monkeypatch.setattr(
        oneshot, "vision_complete",
        lambda **kw: (seen.append(kw), "described")[1],
    )
    entry, root = _plugin(
        tmp_path,
        f"""
        import asyncio
        from agent_runtime.infrastructure.llm.oneshot import vision_complete
        try:
            out = await asyncio.to_thread(
                vision_complete, model="test/brain-model", prompt="what is this?",
                image_paths=[r"{secret}"]
            )
            return ToolResult.text("LEAKED: " + out)
        except RuntimeError as e:
            return ToolResult.text("refused: " + str(e)[:60])
        """,
    )
    result = _run(_config(), entry, root, workspace)
    assert "LEAKED" not in _text(result)
    assert seen == []


# ─────────────────────────── the deadline ───────────────────────────


def test_a_slow_host_call_does_not_kill_the_tool(tmp_path, workspace, monkeypatch):
    """The tool's clock counts the TOOL's time. Charging it for a model call the HOST is making on
    its behalf produces kills that depend on how busy the provider is — unreproducible by design."""
    monkeypatch.setattr(
        oneshot, "text_complete",
        lambda **kw: (time.sleep(3), "slow answer")[1],
    )
    entry, root = _plugin(tmp_path, CALL_MODEL)
    grant = CapabilityGrant(
        fs_paths=(str(workspace),), models=("test/brain-model",), timeout_s=2.0
    )
    result = _run(_config(), entry, root, workspace, grant=grant)
    assert not result.is_error, _text(result)
    assert "slow answer" in _text(result)


def test_a_tool_that_hangs_on_its_own_time_is_still_killed(tmp_path, workspace, host_model):
    """The pause must not become a way to opt out of the deadline entirely."""
    entry, root = _plugin(
        tmp_path,
        """
        import time
        time.sleep(30)
        return ToolResult.text("should never get here")
        """,
    )
    grant = CapabilityGrant(fs_paths=(str(workspace),), models=("test/brain-model",), timeout_s=2.0)
    result = _run(_config(), entry, root, workspace, grant=grant)
    assert result.is_error
    assert "exceeded its 2s limit" in _text(result)


# ─────────────────────────── the resolver's half ───────────────────────────


def test_the_resolver_grants_no_models_to_a_tool_that_does_not_declare_one():
    resolver = DefaultCapabilityResolver(config=_config())
    grant = resolver.resolve("p", None, None, SimpleNamespace(needs_model=False))
    assert grant.models == ()


def test_a_text_tool_that_declares_needs_model_inherits_the_brain():
    """Matches the documented rule a plugin itself follows: `resolve_model(...) or brain_model(...)`."""
    resolver = DefaultCapabilityResolver(config=_config())
    tool = SimpleNamespace(needs_model=True, model_kind="text", plugin="probeplug", name="probe", default_model="")
    assert "test/brain-model" in resolver.resolve("probeplug", None, None, tool).models


def test_an_operator_can_pin_the_allowed_models_outright():
    resolver = DefaultCapabilityResolver(config=_config(sandbox_models=("only/this-one",)))
    tool = SimpleNamespace(needs_model=True, model_kind="text", plugin="p", name="t", default_model="")
    assert resolver.resolve("p", None, None, tool).models == ("only/this-one",)


def test_a_tools_own_configured_model_is_granted():
    config = _config(plugins={"probeplug": {"tools": {"probe": {"model": "configured/model"}}}})
    resolver = DefaultCapabilityResolver(config=config)
    tool = SimpleNamespace(needs_model=True, model_kind="text", plugin="probeplug", name="probe", default_model="")
    assert "configured/model" in resolver.resolve("probeplug", None, None, tool).models


def test_the_grant_payload_carries_model_ids_but_never_a_key():
    from agent_runtime.infrastructure.tools.sandbox import protocol

    grant = CapabilityGrant(models=("a/b",), secrets={"OPENAI_API_KEY": "sk-real"})
    payload = protocol.grant_payload(grant)
    assert payload["models"] == ["a/b"]
    assert "sk-real" not in str(payload)


# ─────────────────────────── who pays ───────────────────────────


def test_the_call_is_billed_to_the_account_running_the_agent(tmp_path, workspace, monkeypatch):
    """The user's own words on this: the account operating the agent pays. A plugin's model call is
    the agent's spend, so it has to land on the same ledger as everything else that turn — not on
    the platform, and not on whoever happened to publish the plugin.

    The mechanism is that the broker's task inherits the run's context, which carries the account.
    Asserting the account SEEN AT METERING TIME is the only way to catch that silently breaking:
    the call would still succeed, and the bill would just go somewhere else.
    """
    from agent_runtime.infrastructure import accounts

    billed: list = []

    def add_usage(model, in_tokens, out_tokens):
        billed.append({"account": accounts.account_id(), "model": model})

    monkeypatch.setattr(accounts, "add_usage", add_usage)
    monkeypatch.setattr(
        oneshot, "text_complete",
        lambda **kw: (accounts.add_usage(kw["model"], 10, 20), "answer")[1],
    )

    entry, root = _plugin(tmp_path, CALL_MODEL)
    token = accounts.set_account({"account_id": "acct_payer"})
    try:
        result = _run(_config(), entry, root, workspace)
    finally:
        accounts.reset_account(token)

    assert not result.is_error, _text(result)
    assert billed == [{"account": "acct_payer", "model": "test/brain-model"}]


def test_a_desktop_run_bills_nobody_and_still_works(tmp_path, workspace, host_model):
    """No accounts in play (a desktop daemon serving one person) must not become an error path."""
    entry, root = _plugin(tmp_path, CALL_MODEL)
    assert not _run(_config(), entry, root, workspace).is_error

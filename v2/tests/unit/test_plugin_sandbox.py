"""Unit tests for the plugin-sandbox seam (the pure-code, no-infra layer).

Covers: the classifier (origin from provenance tags + config exemption), wrap_untrusted gating
(dormant off, wraps untrusted on, leaves trusted alone), the SandboxedTool transparency + routing,
the LocalPluginSandbox passthrough, and the DefaultCapabilityResolver's default-deny grant.
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import RunContext, set_run_context
from agent_runtime.domain.sandbox import DENY_ALL, CapabilityGrant, PluginOrigin
from agent_runtime.infrastructure.tools.sandbox import (
    DefaultCapabilityResolver,
    LocalPluginSandbox,
    SandboxedTool,
    classify_origin,
    wrap_untrusted,
)


class _FakeTool(Tool):
    """A minimal tool that echoes its params and records the last call it saw."""

    def __init__(self, name="echo", plugin_id="", agent_id=""):
        self.name = name
        self.description = "echo tool"
        self.parameters = {"type": "object", "properties": {}}
        self.label = name
        self.last = None
        # provenance tags exactly as plugin discovery stamps them
        if plugin_id:
            self._plugin_id = plugin_id
        if agent_id:
            self._agent_id = agent_id
        # a bit of model metadata to prove transparent forwarding
        self.needs_model = True
        self.default_timeout_sec = 42.0

    async def execute(self, tool_call_id, params, abort, on_update=None):
        self.last = (tool_call_id, params)
        return ToolResult.text(f"ran {self.name} with {params}")


def _cfg(**kw):
    base = dict(
        sandbox_untrusted_plugins=False,
        sandbox_trusted_plugins=(),
        sandbox_trusted_agents=(),
    )
    base.update(kw)
    return SimpleNamespace(**base)


# --- classifier -------------------------------------------------------------
# Trust follows PROVENANCE, not location: owning tools is not suspicious, arriving inside
# someone else's .agentpkg is. The marketplace ledger is the evidence.
def test_classify_unknown_ledger_fails_closed():
    """No ledger supplied => we cannot prove the agent shipped with the product => untrusted."""
    t = _FakeTool(plugin_id="weather", agent_id="weatherbot")
    assert classify_origin(t) is PluginOrigin.THIRD_PARTY_BUNDLE
    assert classify_origin(t, _cfg(), None).is_untrusted


def test_classify_installed_agent_is_untrusted():
    """The agent is in the ledger => marketplace.install laid it down => someone else's code."""
    t = _FakeTool(plugin_id="weather", agent_id="weatherbot")
    origin = classify_origin(t, _cfg(), frozenset({"weatherbot"}))
    assert origin is PluginOrigin.THIRD_PARTY_BUNDLE


def test_classify_shipped_or_local_agent_is_trusted():
    """Not in the ledger => it shipped with the product or the user authored it here. Owning
    tools must NOT by itself make an agent hostile — that was the old blanket ban."""
    t = _FakeTool(plugin_id="agent-builder", agent_id="agent-builder")
    origin = classify_origin(t, _cfg(), frozenset({"someone-elses-agent"}))
    assert origin is PluginOrigin.FIRST_PARTY
    assert not origin.is_untrusted


def test_classify_empty_ledger_trusts_agent_tools():
    """A fresh install has an empty ledger — that is 'nothing installed', not 'unknown'."""
    t = _FakeTool(plugin_id="mine", agent_id="my-local-agent")
    assert classify_origin(t, _cfg(), frozenset()) is PluginOrigin.FIRST_PARTY


def test_classify_shared_plugin_is_trusted():
    t = _FakeTool(plugin_id="core_fs")  # no _agent_id => shared/first-party tier
    assert classify_origin(t) is PluginOrigin.FIRST_PARTY
    assert not classify_origin(t).is_untrusted


def test_config_can_exempt_a_plugin():
    t = _FakeTool(plugin_id="mine", agent_id="myagent")
    cfg = _cfg(sandbox_trusted_plugins=("mine",))
    assert classify_origin(t, cfg) is PluginOrigin.FIRST_PARTY


def test_config_can_vouch_for_an_installed_agent():
    """The per-agent override outranks the ledger — an operator vouching for a publisher."""
    t = _FakeTool(plugin_id="weather", agent_id="weatherbot")
    cfg = _cfg(sandbox_trusted_agents=("weatherbot",))
    assert classify_origin(t, cfg, frozenset({"weatherbot"})) is PluginOrigin.FIRST_PARTY


def test_agent_cannot_vouch_for_itself():
    """Trust comes from the installer's ledger and config — never from anything the package
    carries. A tool tagged with its own agent id gets no say."""
    t = _FakeTool(plugin_id="evil", agent_id="evil-agent")
    t.trusted = True  # whatever a package might try to declare
    assert classify_origin(t, _cfg(), frozenset({"evil-agent"})) is PluginOrigin.THIRD_PARTY_BUNDLE


# --- wrap gating ------------------------------------------------------------
def test_wrap_dormant_when_disabled():
    t = _FakeTool(plugin_id="weather", agent_id="weatherbot")
    out = wrap_untrusted([t], sandbox=LocalPluginSandbox(),
                         resolver=DefaultCapabilityResolver(), config=_cfg())
    assert out == [t]  # identity: nothing wrapped


def test_wrap_wraps_untrusted_leaves_trusted():
    untrusted = _FakeTool(name="a", plugin_id="weather", agent_id="weatherbot")
    trusted = _FakeTool(name="b", plugin_id="core_fs")
    out = wrap_untrusted([untrusted, trusted], sandbox=LocalPluginSandbox(),
                         resolver=DefaultCapabilityResolver(),
                         config=_cfg(sandbox_untrusted_plugins=True))
    assert isinstance(out[0], SandboxedTool)
    assert out[1] is trusted


def test_wrap_honours_the_ledger():
    """Two agents, both owning tools: only the INSTALLED one gets sandboxed."""
    downloaded = _FakeTool(name="a", plugin_id="weather", agent_id="weatherbot")
    shipped = _FakeTool(name="b", plugin_id="agent-builder", agent_id="agent-builder")
    out = wrap_untrusted([downloaded, shipped], sandbox=LocalPluginSandbox(),
                         resolver=DefaultCapabilityResolver(),
                         config=_cfg(sandbox_untrusted_plugins=True),
                         installed_agent_ids=frozenset({"weatherbot"}))
    assert isinstance(out[0], SandboxedTool)
    assert out[1] is shipped


# --- transparency + routing -------------------------------------------------
def _unwrap(tool):
    """Peel reliability/sandbox wrappers to the metadata-bearing tool, like the catalog does."""
    seen = 0
    while getattr(tool, "_inner", None) is not None and seen < 8:
        tool = tool._inner
        seen += 1
    return tool


def test_sandboxed_tool_is_transparent():
    inner = _FakeTool(name="echo", plugin_id="weather", agent_id="weatherbot")
    st = SandboxedTool(inner, LocalPluginSandbox(), DefaultCapabilityResolver(),
                       plugin_id="weather", origin=PluginOrigin.THIRD_PARTY_BUNDLE)
    # Tool contract forwards
    assert st.name == "echo"
    assert st.parameters == inner.parameters
    assert st.concurrency == "parallel"
    # the catalog reads model metadata by PEELING _inner (not off the wrapper) — expose it
    assert _unwrap(st) is inner
    assert _unwrap(st).needs_model is True
    # per-tool policy defaults (not Tool class attrs) forward via __getattr__ for resolve_policy
    assert st.default_timeout_sec == 42.0
    # instance-level provenance forwards via __getattr__
    assert st._agent_id == "weatherbot"


def test_local_sandbox_passthrough_runs_inner():
    inner = _FakeTool(name="echo", plugin_id="weather", agent_id="weatherbot")
    sandbox = LocalPluginSandbox()
    st = SandboxedTool(inner, sandbox, DefaultCapabilityResolver(),
                       plugin_id="weather", origin=PluginOrigin.THIRD_PARTY_BUNDLE)
    res = asyncio.run(st.execute("call-1", {"x": 1}, asyncio.Event()))
    assert not res.is_error
    assert "ran echo" in res.content[0].text
    assert inner.last == ("call-1", {"x": 1})  # the real tool actually ran


def test_local_sandbox_missing_tool_is_error():
    sandbox = LocalPluginSandbox()  # nothing registered
    res = asyncio.run(
        sandbox.run_tool("ghost", "nope", "c", {}, asyncio.Event(), grant=DENY_ALL)
    )
    assert res.is_error
    assert "not loaded" in res.content[0].text


# --- default resolver: default-deny -----------------------------------------
def test_default_grant_denies_secrets_and_net():
    r = DefaultCapabilityResolver()
    g = r.resolve("weather", PluginOrigin.THIRD_PARTY_BUNDLE, None)
    assert g.secrets == {}          # THE invariant: sandbox is blind to keys
    assert g.net_allowlist == ()
    assert g.fs_paths == ()         # no ctx => no workspace granted
    assert g.timeout_s > 0


def test_default_grant_scopes_to_workspace():
    r = DefaultCapabilityResolver()
    ctx = RunContext(agent_id="weatherbot", session_key="s", mode="chat", workspace="/data/ws")
    g = r.resolve("weather", PluginOrigin.THIRD_PARTY_BUNDLE, ctx)
    assert g.fs_paths == ("/data/ws",)
    assert g.secrets == {}


# --- the ledger that drives the trust decision ------------------------------
def test_installed_ids_missing_ledger_is_empty_not_unknown(tmp_path):
    """A fresh install has no ledger. That means 'nothing installed' — agent tools stay
    trusted. Returning None here would sandbox every shipped agent on a clean machine."""
    from agent_runtime.infrastructure.marketplace.installed_store import JsonInstalledStore

    store = JsonInstalledStore(tmp_path / "installed_bundles.json")
    assert store.installed_ids() == frozenset()


def test_installed_ids_corrupt_ledger_is_unknown(tmp_path):
    """Unreadable ledger must NOT look like an empty one — that would silently promote
    downloaded code to first-party. None makes the caller fail closed."""
    from agent_runtime.infrastructure.marketplace.installed_store import JsonInstalledStore

    path = tmp_path / "installed_bundles.json"
    path.write_text("{not json", encoding="utf-8")
    assert JsonInstalledStore(path).installed_ids() is None


def test_installed_ids_reads_bundle_ids(tmp_path):
    """Bundle id IS agent id — unpack_bundle writes to agents_dir/<manifest.id>."""
    import json

    from agent_runtime.infrastructure.marketplace.installed_store import JsonInstalledStore

    path = tmp_path / "installed_bundles.json"
    path.write_text(
        json.dumps({"bundles": [{"id": "weatherbot", "version": "1.0.0"}, {"id": "chef"}]}),
        encoding="utf-8",
    )
    assert JsonInstalledStore(path).installed_ids() == frozenset({"weatherbot", "chef"})


def test_resolver_error_falls_back_to_deny_all():
    class Boom:
        def resolve(self, *a, **k):
            raise RuntimeError("resolver bug")

    inner = _FakeTool(name="echo", plugin_id="weather", agent_id="weatherbot")
    sandbox = LocalPluginSandbox()
    st = SandboxedTool(inner, sandbox, Boom(),
                       plugin_id="weather", origin=PluginOrigin.THIRD_PARTY_BUNDLE)
    # must not raise, must still run (grant falls back to DENY_ALL, passthrough ignores it)
    res = asyncio.run(st.execute("c", {}, asyncio.Event()))
    assert not res.is_error

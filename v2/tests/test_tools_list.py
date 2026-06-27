"""tools.list -- enumerate the live catalog (full), or the subset a given agent sees (its
allow/deny scope). Service method + the gateway surface clients query."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.application.services.agent_service import AgentService
from agentd.domain.agent import AgentSpec


class FT:                                   # a duck-typed fake tool
    def __init__(self, name, desc="", label="", conc="parallel"):
        self.name, self.description, self.label, self.concurrency = name, desc, label, conc


def _spec(aid, allow=None, deny=()):
    return AgentSpec(id=aid, name=aid, workspace=Path("."), state_dir=Path("."),
                     tools_allow=allow, tools_deny=tuple(deny))


class _Reg:
    def __init__(self, specs):
        self._s = {s.id: s for s in specs}

    def get(self, aid):
        return self._s[aid]                 # KeyError if missing -> service falls back

    def resolve(self, _key):
        return self._s["main"]


def _svc(tools, specs):
    return AgentService(engine=object(), tools=list(tools), registry=_Reg(specs),
                        make_session=lambda *a, **k: None, build_prompt=lambda *a, **k: "")


def test_full_catalog_lists_every_tool_with_summary_and_source():
    svc = _svc([FT("read", "Read a file.\n(more)"), FT("google__gmail_send", "Send mail.")],
               [_spec("main")])
    info = svc.list_tools()
    assert [t["name"] for t in info] == ["read", "google__gmail_send"]
    assert info[0]["summary"] == "Read a file."        # first line only
    assert info[0]["source"] == "tool"
    assert info[1]["source"] == "mcp"                  # namespaced server__tool


def test_per_agent_view_applies_allow_deny():
    tools = [FT("read"), FT("write"), FT("simple_login")]
    specs = [_spec("main"), _spec("sakana", deny=["simple_login"])]
    svc = _svc(tools, specs)
    assert [t["name"] for t in svc.list_tools("sakana")] == ["read", "write"]   # denied one gone
    assert [t["name"] for t in svc.list_tools()] == ["read", "write", "simple_login"]


def test_per_agent_allowlist():
    tools = [FT("read"), FT("write"), FT("browser")]
    svc = _svc(tools, [_spec("main"), _spec("ro", allow=("read",))])
    assert [t["name"] for t in svc.list_tools("ro")] == ["read"]


def test_unknown_agent_falls_back_to_full_catalog():
    svc = _svc([FT("read")], [_spec("main")])
    assert [t["name"] for t in svc.list_tools("ghost")] == ["read"]


# ── gateway surface ──────────────────────────────────────────────────────────

def test_gateway_tools_list_passes_agent_and_count():
    from agentd.presentation.gateway import Gateway

    class _Svc:
        def list_tools(self, agent_id=None):
            return [{"name": "read"}] if agent_id is None else [{"name": "write"}]

    gw = Gateway(config=object(), service=_Svc())
    full = gw._tools_list({})
    assert full["tools"] == [{"name": "read"}] and full["count"] == 1 and full["agentId"] is None
    scoped = gw._tools_list({"agentId": "sakana"})
    assert scoped["agentId"] == "sakana" and scoped["tools"] == [{"name": "write"}]

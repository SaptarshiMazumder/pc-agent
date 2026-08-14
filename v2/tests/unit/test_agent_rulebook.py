"""The agent rulebook — creation-time id rules, the fail-closed ownership stamp, the
installed write-scope clamp, the portability checks, and the ONE policy table the gates read.

Three enforcement tiers, pinned together because they are one system:
  runtime refusals (cannot be dodged)  ->  domain/agent.py id rules, registry stamp,
                                           agent_service clamp
  validator findings (pay-per-violation) -> portability_rules, packageability workspace INFO
  gate policy (one table)               -> rulebook.blockers(PACK/PUBLISH), publish tool
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parents[2]
        / "agents" / "agent-builder" / "plugins" / "agent-authoring"
    ),
)

import pytest

from agent_runtime.domain.agent import (
    RESERVED_AGENT_IDS,
    invalid_new_agent_id,
)


# ── creation-time id rules: one authority, every path ──────────────────────────────────


def test_ordinary_ids_pass():
    for good in ("support-bot", "note_taker", "a", "x2", "marketing-agent"):
        assert invalid_new_agent_id(good) == ""


@pytest.mark.parametrize("reserved", sorted(RESERVED_AGENT_IDS))
def test_every_reserved_id_is_refused_with_a_reason(reserved):
    assert "reserved" in invalid_new_agent_id(reserved)


def test_shape_rules():
    assert "empty" in invalid_new_agent_id("")
    assert "start" in invalid_new_agent_id("---")
    assert "start" in invalid_new_agent_id("-agent")
    assert "letters" in invalid_new_agent_id("agent!x")
    assert "longer" in invalid_new_agent_id("a" * 65)
    assert invalid_new_agent_id("a" * 64) == ""


def test_the_registry_refuses_a_reserved_id_at_create(tmp_path):
    from agent_runtime.infrastructure.agents.file_registry import FileAgentRegistry

    reg = FileAgentRegistry(
        SimpleNamespace(
            agents_dir=str(tmp_path / "agents"),
            state_dir=tmp_path / "state",
            workspace=str(tmp_path / "ws"),
            model="",
            agent_name="",
        )
    )
    with pytest.raises(ValueError, match="reserved"):
        reg.create_from("workspace", lambda d: None)
    with pytest.raises(ValueError, match="start"):
        reg.create_from("---", lambda d: None)


# ── the installed write-scope clamp: origin is data, curated/authored keep their grant ──


def _service_with_origin(origin):
    from agent_runtime.application.services.agent_service import AgentService

    return AgentService(
        engine=None,
        tools=[],
        registry=SimpleNamespace(
            get=lambda _id: (_ for _ in ()).throw(KeyError(_id)),
            origin_of=lambda _id: origin,
        ),
        make_session=lambda sid, agent: None,
        build_prompt=lambda *a, **k: "",
    )


def test_an_installed_agents_wide_roots_collapse_to_its_own_folder(tmp_path):
    agent_dir = tmp_path / "agents" / "helper"
    agent_dir.mkdir(parents=True)
    elsewhere = str(tmp_path / "agents")  # wider than the agent itself
    agent = SimpleNamespace(id="helper", dir=str(agent_dir))
    svc = _service_with_origin("installed")
    clamped = svc._installed_write_clamp(agent, (elsewhere,))
    assert clamped == (str(agent_dir),), "outside roots dropped; own dir kept (never empty)"
    inside = str(agent_dir / "notes")
    assert svc._installed_write_clamp(agent, (inside, elsewhere)) == (inside,)


@pytest.mark.parametrize("origin", ["authored", "curated"])
def test_authored_and_curated_keep_their_declared_scope(tmp_path, origin):
    agent = SimpleNamespace(id="builder", dir=str(tmp_path))
    wide = (str(tmp_path.parent),)
    assert _service_with_origin(origin)._installed_write_clamp(agent, wide) == wide


def test_a_registry_without_provenance_clamps_nothing(tmp_path):
    from agent_runtime.application.services.agent_service import AgentService

    svc = AgentService(
        engine=None,
        tools=[],
        registry=SimpleNamespace(get=lambda _id: (_ for _ in ()).throw(KeyError(_id))),
        make_session=lambda sid, agent: None,
        build_prompt=lambda *a, **k: "",
    )
    agent = SimpleNamespace(id="x", dir=str(tmp_path))
    wide = (str(tmp_path.parent),)
    assert svc._installed_write_clamp(agent, wide) == wide


def test_an_empty_declaration_stays_empty_meaning_unrestricted_is_untouched(tmp_path):
    agent = SimpleNamespace(id="x", dir=str(tmp_path))
    assert _service_with_origin("installed")._installed_write_clamp(agent, ()) == ()


# ── portability rules: fine on the author's desktop, wrong where the agent is going ────


def _portability(raw):
    from agent_authoring.domain.portability_rules import PortabilityRules

    return {f.code for f in PortabilityRules().check(None, raw, [])}


def test_wide_write_roots_is_flagged_and_self_scope_is_not():
    assert "WIDE_WRITE_ROOTS" in _portability(
        {"tools": {"fs": {"write_roots": ["<agents_dir>"]}}}
    )
    assert "WIDE_WRITE_ROOTS" not in _portability(
        {"tools": {"fs": {"write_roots": ["<agent_dir>", "<agent_dir>/out"]}}}
    )
    assert _portability({}) == set()


def test_exec_on_a_web_delivery_is_flagged():
    raw = {"delivery": {"web": True}, "tools": {"allow": ["read", "exec", "process"]}}
    assert "EXEC_ON_WEB" in _portability(raw)
    assert "EXEC_ON_WEB" not in _portability({"tools": {"allow": ["exec"]}})  # no web delivery
    assert "EXEC_ON_WEB" not in _portability({"delivery": {"web": True}})  # no shell granted


def test_web_plus_requires_local_is_a_contradiction():
    assert "WEB_REQUIRES_LOCAL" in _portability(
        {"delivery": {"web": True}, "requires_local": True}
    )
    assert "WEB_REQUIRES_LOCAL" not in _portability({"requires_local": True})


def test_a_heartbeat_without_autonomy_never_fires_and_says_so():
    assert "HEARTBEAT_WITHOUT_AUTONOMY" in _portability({"heartbeat": "30m"})
    assert "HEARTBEAT_WITHOUT_AUTONOMY" not in _portability(
        {"heartbeat": "30m", "capabilities": {"autonomy": True}}
    )


def test_workspace_contents_produce_the_not_shipped_info():
    from agent_authoring.domain.packageability_rules import PackageabilityRules

    codes = {
        f.code
        for f in PackageabilityRules().check(
            None, {"version": "1.0.0"}, ["workspace/seed.csv", "templates/a.md"]
        )
    }
    assert "WORKSPACE_NOT_SHIPPED" in codes


# ── the rulebook: one table decides what the gates refuse ──────────────────────────────


def test_the_policy_rows_that_guard_something():
    from agent_authoring.domain.rulebook import PACK, PUBLISH, blockers

    pack, publish = blockers(PACK), blockers(PUBLISH)
    # a version-less publish can never be superseded; local packing stays permissive
    assert "NO_VERSION" in publish and "NO_VERSION" not in pack
    # builder-grade reach ships nowhere, side-loads included
    assert "WIDE_WRITE_ROOTS" in pack and "WIDE_WRITE_ROOTS" in publish
    # web deliveries that cannot work refuse at the listing gate
    assert {"EXEC_ON_WEB", "WEB_REQUIRES_LOCAL"} <= publish
    # the four sandbox certainties still block both, as before the table existed
    for code in (
        "UNTRUSTED_WANTS_SECRETS",
        "UNTRUSTED_WANTS_NETWORK",
        "UNTRUSTED_WANTS_SPAWN",
        "UNTRUSTED_MODEL_UNDECLARED",
    ):
        assert code in pack and code in publish
    # heuristics never gate a release
    assert "UNTRUSTED_MAYBE_NETWORK" not in pack | publish


def test_apply_policy_reprices_only_what_a_row_overrides():
    from agent_authoring.domain.finding import WARN, Finding
    from agent_authoring.domain.rulebook import apply_policy

    unknown = Finding(level=WARN, code="SOME_FUTURE_CODE", message="m")
    listed = Finding(level=WARN, code="NO_VERSION", message="m")
    out = apply_policy((unknown, listed))
    assert [f.level for f in out] == [WARN, WARN], "no row sets a level today — nothing repriced"


def test_every_emitted_code_is_catalogued_in_the_rulebook():
    """The table is the whole catalogue. A rule module that emits a code with no row means the
    catalogue lies — a screw someone tries to turn from the table and cannot find. This scans
    every rules module for `code="X"` and asserts X has a row, so a NEW rule cannot skip it."""
    import re

    from agent_authoring.domain.rulebook import RULEBOOK

    domain = (
        Path(__file__).resolve().parents[2]
        / "agents" / "agent-builder" / "plugins" / "agent-authoring" / "agent_authoring" / "domain"
    )
    emitted: set[str] = set()
    for module in domain.glob("*_rules.py"):
        emitted |= set(re.findall(r'code="([A-Z_]+)"', module.read_text(encoding="utf-8")))
    missing = emitted - set(RULEBOOK)
    assert not missing, f"emitted but not in the RULEBOOK table: {sorted(missing)}"


def test_publish_refuses_on_a_rulebook_blocker_even_at_warn_level(tmp_path):
    import asyncio

    from agent_authoring.domain.finding import WARN, Finding
    from agent_authoring.domain.report import Report
    from agent_authoring.presentation.publish_agent_tool import PublishAgentTool

    agent_dir = tmp_path / "shippable"
    agent_dir.mkdir()
    (agent_dir / "agent.toml").write_text("name='s'", encoding="utf-8")
    registry = SimpleNamespace(
        resolve_dir=lambda _id: agent_dir,
        owns=lambda _id: True,
        origin_of=lambda _id: "authored",
    )
    validator = SimpleNamespace(
        validate=lambda _id: Report(
            agent_id=_id,
            findings=(Finding(level=WARN, code="NO_VERSION", message="no version", fix="add one"),),
        )
    )
    tool = PublishAgentTool(SimpleNamespace(), registry, validator)
    result = asyncio.run(tool.execute("t1", {"agent_id": "shippable"}, None))
    assert result.is_error
    text = result.content[0].text
    assert "NO_VERSION" in text and "Nothing was built or sent" in text


def test_publish_refuses_every_non_authored_origin(tmp_path):
    import asyncio

    from agent_authoring.presentation.publish_agent_tool import PublishAgentTool

    agent_dir = tmp_path / "someone-elses"
    agent_dir.mkdir()
    (agent_dir / "agent.toml").write_text("name='s'", encoding="utf-8")
    for origin in ("installed", "curated", "web-app"):
        registry = SimpleNamespace(
            resolve_dir=lambda _id: agent_dir,
            owns=lambda _id: True,
            origin_of=lambda _id, o=origin: o,
        )
        tool = PublishAgentTool(SimpleNamespace(), registry, None)
        result = asyncio.run(tool.execute("t1", {"agent_id": "someone-elses"}, None))
        assert result.is_error, origin

"""sessions.list — single-agent tagging + cross-agent Recents / project filtering.

Covers the Phase A1 change: _sessions_list now tags every row with its owning agentId,
and supports `all` (merge every agent's chats) and `projectId` (cross-agent, one project)
scans that EXCLUDE internal agent-to-agent / cron sessions (on-disk `agent_…` stems).
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_runtime.infrastructure.memory.local_store import SessionStore, write_session_meta


def _gateway(root: Path, agents: dict):
    """A gateway whose registry maps each agent id -> its own state_dir under `root`."""
    from agent_runtime.presentation.gateway import Gateway

    return Gateway(
        config=SimpleNamespace(state_dir=root),
        service=None,
        registry=SimpleNamespace(
            list_ids=lambda: list(agents),
            get=lambda a: SimpleNamespace(state_dir=agents[a]),
        ),
    )


def test_single_agent_rows_carry_agent_id(tmp_path):
    main_dir = tmp_path / "agents" / "main"
    SessionStore(main_dir, "desk-1").load()
    gw = _gateway(tmp_path, {"main": main_dir})

    out = gw._sessions_list({"agentId": "main"})
    assert out["agentId"] == "main"
    assert [r["sessionId"] for r in out["sessions"]] == ["desk-1"]
    assert out["sessions"][0]["agentId"] == "main"  # every row now tagged


def test_all_merges_across_agents_and_hides_internal(tmp_path):
    main_dir = tmp_path / "agents" / "main"
    support_dir = tmp_path / "agents" / "support"
    SessionStore(main_dir, "desk-main").load()
    SessionStore(support_dir, "desk-support").load()
    # an internal agent-to-agent/cron session (key `agent:support:main` -> `agent_...` stem)
    SessionStore(support_dir, "agent_support_main").load()

    gw = _gateway(tmp_path, {"main": main_dir, "support": support_dir})
    out = gw._sessions_list({"all": True})

    by_id = {r["sessionId"]: r["agentId"] for r in out["sessions"]}
    assert by_id == {"desk-main": "main", "desk-support": "support"}  # internal excluded
    assert out["all"] is True


def test_project_id_filters_across_agents(tmp_path):
    main_dir = tmp_path / "agents" / "main"
    support_dir = tmp_path / "agents" / "support"
    for d, sid in ((main_dir, "desk-a"), (support_dir, "desk-b"), (main_dir, "desk-c")):
        SessionStore(d, sid).load()
    write_session_meta(main_dir, "desk-a", projectId="proj-x")
    write_session_meta(support_dir, "desk-b", projectId="proj-x")
    write_session_meta(main_dir, "desk-c", projectId="proj-other")

    gw = _gateway(tmp_path, {"main": main_dir, "support": support_dir})
    out = gw._sessions_list({"projectId": "proj-x"})

    got = {(r["sessionId"], r["agentId"]) for r in out["sessions"]}
    assert got == {("desk-a", "main"), ("desk-b", "support")}  # cross-agent, one project
    assert out["projectId"] == "proj-x"

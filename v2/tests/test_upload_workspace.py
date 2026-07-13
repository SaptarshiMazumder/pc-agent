"""Uploads land in the PROJECT's shared workspace when a chat is inside a project (plan §11), else
the agent's own — so an uploaded image and the tool outputs that land next to it stay together,
instead of everything dumping in main's uploads."""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.infrastructure.memory import projects_store
from agentd.presentation.gateway import Gateway


class _G:
    """Minimal stand-in exposing the gateway's upload-workspace resolution."""

    def __init__(self, state_dir, agent_ws):
        self.config = SimpleNamespace(state_dir=str(state_dir), workspace=str(agent_ws))
        self.registry = None  # no named agent -> default (config.workspace)

    _upload_workspace = Gateway._upload_workspace
    _resolve_workspace = Gateway._resolve_workspace


def test_project_chat_uploads_go_to_project_workspace(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    agent_ws = tmp_path / "agent_ws"
    proj = projects_store.create_project(state, "My Project")
    g = _G(state, agent_ws)

    ws = g._upload_workspace(None, proj["id"])
    expected = str(projects_store.project_workspace_dir(state, proj["id"]))
    assert ws == expected
    assert "projects" in ws and proj["id"] in ws


def test_standalone_chat_uploads_stay_on_agent_workspace(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    agent_ws = tmp_path / "agent_ws"
    g = _G(state, agent_ws)
    # no projectId -> the agent/default workspace (unchanged behavior)
    assert g._upload_workspace(None, "") == str(agent_ws)


def test_unknown_project_falls_back_to_agent_workspace(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    agent_ws = tmp_path / "agent_ws"
    g = _G(state, agent_ws)
    assert g._upload_workspace(None, "does-not-exist") == str(agent_ws)

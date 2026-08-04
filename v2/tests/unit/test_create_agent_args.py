"""create_agent's SCHEMA, exercised the way the engine exercises it.

These go through `validate_args` — the jsonschema gate that runs BEFORE `execute`. That
distinction is the whole point of the file: the tool's own tests called `execute()` directly,
so a schema that contradicted the implementation passed everything and still failed in the
product. It declared `action` required while `execute` defaulted it to "create", so the most
natural call the model can make — create_agent(id=..., identity=...) — was rejected by the
validator before the default could apply.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agent_runtime.application.interfaces.tool import ToolArgError, validate_args
from agent_authoring.presentation.create_agent_tool import CreateAgentTool


class _Registry:
    """Just enough registry for the tool: a roster and an agents dir."""

    def __init__(self, tmp):
        self.agents_dir = str(tmp)
        self.added = []

    def list_ids(self):
        return ["main"]

    def add(self, agent_id):
        self.added.append(agent_id)


def _tool(tmp_path):
    return CreateAgentTool(_Registry(tmp_path))


# --- the schema gate --------------------------------------------------------
def test_action_may_be_omitted(tmp_path):
    """THE REGRESSION: the model's natural first call must survive validation."""
    args = {"id": "gym-log", "identity": "I track gym sets."}
    assert validate_args(_tool(tmp_path), args) == args


def test_omitted_action_creates(tmp_path):
    """...and the default that requiring `action` made unreachable actually applies."""
    tool = _tool(tmp_path)
    args = validate_args(tool, {"id": "gym-log", "identity": "I track gym sets."})
    result = asyncio.run(tool.execute("c1", args, asyncio.Event()))
    assert not result.is_error
    assert (tmp_path / "gym-log" / "agent.toml").is_file()
    assert (tmp_path / "gym-log" / "IDENTITY.md").is_file()


def test_explicit_actions_still_validate(tmp_path):
    for action in ("create", "update", "list"):
        args = {"action": action, "id": "gym-log", "identity": "x"}
        assert validate_args(_tool(tmp_path), args)["action"] == action


def test_unknown_action_is_still_rejected(tmp_path):
    """Relaxing `required` must not relax the enum — a typo'd action is still caught here,
    not silently treated as a create."""
    with pytest.raises(ToolArgError):
        validate_args(_tool(tmp_path), {"action": "destroy", "id": "x", "identity": "y"})


def test_every_property_documents_itself(tmp_path):
    """`action` was the one property with no description — the reason the model had to guess
    at it in the first place. Keep that from coming back."""
    props = _tool(tmp_path).parameters["properties"]
    undocumented = [k for k, v in props.items() if not v.get("description")]
    assert not undocumented, f"properties missing a description: {undocumented}"


# --- guards that live in execute, not the schema ----------------------------
def test_identity_is_still_required_at_runtime(tmp_path):
    """Not a schema `required` — the tool answers with a sentence the model can act on
    instead of a validation error it has to decode."""
    tool = _tool(tmp_path)
    result = asyncio.run(tool.execute("c1", validate_args(tool, {"id": "x"}), asyncio.Event()))
    assert result.is_error
    assert "identity" in result.content[0].text


def test_main_cannot_be_created(tmp_path):
    tool = _tool(tmp_path)
    args = validate_args(tool, {"id": "main", "identity": "hijack"})
    result = asyncio.run(tool.execute("c1", args, asyncio.Event()))
    assert result.is_error
    assert not (tmp_path / "main").exists()

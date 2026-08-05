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


# --- never clobber an existing agent by accident ----------------------------
# An earlier version answered "already exists — use action='update' to change it", which the
# model followed on its own initiative. That rewrote agent.toml from the skeleton and deleted
# a real agent's [app] table, orphaning its whole ui/ folder. The decision belongs to the user.

def _existing(tmp_path, body):
    d = tmp_path / "victim"
    d.mkdir(parents=True, exist_ok=True)
    (d / "agent.toml").write_text(body, encoding="utf-8")
    (d / "IDENTITY.md").write_text("original\n", encoding="utf-8")
    return d


AUTHORED = (
    'name = "Victim"\nversion = "2.0.0"\ntagline = "hand written"\n\n'
    '[app]\ntitle = "Victim"\nentry = "ui/index.html"\n\n'
    '[tools]\nallow = ["read"]\n\n'
    '[plugins.figures.tools.make_chart]\nmodel = "x"\n'
)


def test_create_on_an_existing_id_refuses_and_offers_no_shortcut(tmp_path):
    _existing(tmp_path, AUTHORED)
    tool = _tool(tmp_path)
    args = validate_args(tool, {"id": "victim", "identity": "new"})
    res = asyncio.run(tool.execute("c", args, asyncio.Event()))
    assert res.is_error
    text = res.content[0].text
    assert "ASK THEM" in text                      # hands the decision back
    assert "DIFFERENT id" in text                  # the new-agent option
    assert "`write`" in text                       # the edit-in-place option
    assert (tmp_path / "victim" / "agent.toml").read_text(encoding="utf-8") == AUTHORED


def test_update_without_confirmation_refuses_and_names_the_losses(tmp_path):
    _existing(tmp_path, AUTHORED)
    tool = _tool(tmp_path)
    args = validate_args(tool, {"action": "update", "id": "victim", "identity": "new"})
    res = asyncio.run(tool.execute("c", args, asyncio.Event()))
    assert res.is_error
    text = res.content[0].text
    for section in ("[app]", "[tools]", "tagline", "[plugins.figures...]"):
        assert section in text, f"{section} must be named before it is destroyed"
    assert (tmp_path / "victim" / "agent.toml").read_text(encoding="utf-8") == AUTHORED


def test_confirmed_rebuild_is_allowed_and_reports_what_it_deleted(tmp_path):
    """Destruction is NOT blocked — it is made deliberate and visible."""
    _existing(tmp_path, AUTHORED)
    tool = _tool(tmp_path)
    args = validate_args(
        tool,
        {"action": "update", "id": "victim", "identity": "new", "confirm_overwrite": True},
    )
    res = asyncio.run(tool.execute("c", args, asyncio.Event()))
    assert not res.is_error
    text = res.content[0].text
    assert "DELETED" in text
    for section in ("[app]", "[tools]", "tagline"):
        assert section in text
    assert res.details["destroyed"]
    assert "[app]" not in (tmp_path / "victim" / "agent.toml").read_text(encoding="utf-8")


def test_creating_a_different_agent_never_touches_the_first(tmp_path):
    """The other half of the user's question: a new id is a new directory, full stop."""
    _existing(tmp_path, AUTHORED)
    tool = _tool(tmp_path)
    args = validate_args(tool, {"id": "tinder-clone", "identity": "swipe"})
    res = asyncio.run(tool.execute("c", args, asyncio.Event()))
    assert not res.is_error
    assert (tmp_path / "victim" / "agent.toml").read_text(encoding="utf-8") == AUTHORED
    assert (tmp_path / "tinder-clone" / "agent.toml").is_file()

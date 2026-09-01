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
from agent_authoring.presentation.create_agent_tool import CreateAgentTool

from agent_runtime.application.interfaces.tool import ToolArgError, validate_args


class _Registry:
    """Just enough registry for the tool, mirroring the REAL contract: the tool authors
    content, the registry owns the world half — resolve_dir answers where an existing
    agent lives, create_from places/collides/registers a new one (FileAgentRegistry also
    stamps ownership there; this stand-in only models the placement contract)."""

    def __init__(self, tmp):
        self.agents_dir = str(tmp)
        self.added = []
        self.created = []

    def list_ids(self):
        return ["main"]

    def add(self, agent_id):
        self.added.append(agent_id)

    def resolve_dir(self, agent_id):
        d = Path(self.agents_dir) / agent_id
        return d if d.is_dir() else None

    def create_from(self, agent_id, write_files, shared=False):
        #  routes a real registry to the shared catalogue; this fake has one root, so the
        # flag only has to be ACCEPTED - the placement decision is the real registry's test.
        from types import SimpleNamespace

        d = Path(self.agents_dir) / agent_id
        if agent_id in self.list_ids() or d.exists():
            raise ValueError(f"agent '{agent_id}' already exists")
        d.mkdir(parents=True, exist_ok=True)
        write_files(d)
        self.created.append(agent_id)
        return SimpleNamespace(dir=str(d))


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


# --- the window arrives WITH the agent --------------------------------------
# THE GUARANTEE THIS PINS. Creating an agent with a window used to be two steps: create it, then
# call `scaffold_react_app`. The second was a hope — the skill asked for it and nothing enforced
# it — and what it produces is the entire structural guarantee, because an agent whose window was
# never scaffolded has no sign-in, no credits page, no settings and no organizations. Every one of
# those is invisible to the author and total for whoever installs the agent.


class _Scaffolder:
    """Stands in for ScaffoldReactAppService. Records that it was asked, and can refuse."""

    def __init__(self, fail: bool = False):
        self.asked: list[str] = []
        self._fail = fail

    def scaffold(self, agent_id: str, template: str = "chat"):
        from types import SimpleNamespace

        if self._fail:
            raise RuntimeError("the skeleton is missing")
        self.asked.append(agent_id)
        return SimpleNamespace(written=["package.json", "src/App.tsx"])


def _said(result) -> str:
    """A ToolResult carries content BLOCKS, not a string."""
    return chr(10).join(getattr(b, "text", "") for b in result.content)


async def _create(tool, **args):
    params = validate_args(tool, {"id": "note-taker", "identity": "I take notes.", **args})
    return await tool.execute("call-1", params, abort=None)


@pytest.mark.asyncio
async def test_a_windowed_agent_is_born_with_its_window(tmp_path):
    scaffolder = _Scaffolder()
    tool = CreateAgentTool(_Registry(tmp_path), scaffolder=scaffolder)

    result = await _create(tool, window=True)

    assert scaffolder.asked == ["note-taker"], "the window was never assembled"
    # ...and agent.toml declares it, or the daemon serves nothing. The two facts are decided
    # together on purpose: a window on disk that agent.toml never mentions cannot be opened.
    toml = (tmp_path / "note-taker" / "agent.toml").read_text(encoding="utf-8")
    assert "[app]" in toml
    assert 'entry = "ui/index.html"' in toml
    said = _said(result).lower()
    assert "do not rebuild it" in said and "src/common/" in said


@pytest.mark.asyncio
async def test_an_agent_with_no_window_gets_neither_app_nor_scaffold(tmp_path):
    """`window` is a decision, not a default. An agent that quietly grew a window nobody asked for
    is the failure the start dialog exists to prevent."""
    scaffolder = _Scaffolder()
    tool = CreateAgentTool(_Registry(tmp_path), scaffolder=scaffolder)

    await _create(tool)

    assert scaffolder.asked == []
    assert "[app]" not in (tmp_path / "note-taker" / "agent.toml").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_a_window_that_cannot_be_assembled_is_reported_not_swallowed(tmp_path):
    """The agent is already created and registered by then, so failing the whole call would tell
    the caller nothing was made when something was. It says what is missing and how to fix it —
    silence here would ship an agent that cannot be published and nobody would know why."""
    tool = CreateAgentTool(_Registry(tmp_path), scaffolder=_Scaffolder(fail=True))

    result = await _create(tool, window=True)

    assert (tmp_path / "note-taker" / "agent.toml").is_file(), "the agent should still exist"
    said = _said(result)
    assert "scaffold_react_app" in said
    assert "the skeleton is missing" in said


@pytest.mark.asyncio
async def test_the_result_says_where_the_agent_actually_is(tmp_path):
    """WHO NEEDS THIS, and why prose was not enough.

    The result text has always ended "… at <path>", which was sufficient while the MODEL made this
    call and read the answer. Creating from the start dialog moved the call into the window, so the
    model now sees only the scope preamble — and a UI cannot parse a path out of a sentence.

    Without it the preamble named `agents/<id>/`, which file tools resolve against the agent's
    WORKSPACE. It resolved to somewhere that does not exist, and the model went hunting through the
    agents directory — where a signed-in caller's agents are not, because those are placed in that
    caller's own account overlay.
    """
    tool = CreateAgentTool(_Registry(tmp_path), scaffolder=_Scaffolder())

    result = await _create(tool, window=True)

    where = result.details["dir"]
    assert Path(where).is_absolute(), "a relative path is what caused the hunt"
    assert (Path(where) / "agent.toml").is_file(), "it must point at the agent, not near it"

"""Where an agent may WRITE — a block in code, not advice in a document.

Agent Builder's job is authoring OTHER agents, so it has to write outside its own workspace.
Before this, "outside its own workspace" meant the whole disk — including the shared `plugins/`
directory. A tool written there is FIRST_PARTY on every machine that installs it: never
sandboxed, regardless of what the agent that carried it along is. That is the one route by which
a capability refused to a private tool (`subprocess`, a raw socket, reading a key) could be
laundered back in.

Two rules, and the second matters as much as the first:

  * roots  — an absolute path outside them is refused
  * deny   — carved out of the roots, and deny WINS, so an agent can be handed a wide root and
             still be kept out of its own definition. An agent that can rewrite its own
             agent.toml can widen its own roots, and then none of this means anything.

EMPTY ROOTS = UNRESTRICTED. Every agent that declares nothing behaves exactly as before; only
one that opts in is constrained. That is deliberate — this is a scope for the agent whose reach
is unusual, not a new default for everybody.

READS ARE NEVER SCOPED. Reading damages nothing, and an agent must be able to read its own skill
and the SDK it vendors into a generated UI.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agent_runtime.application.run_context import RunContext, set_run_context
from agent_runtime.application.services.agent_service import AgentService

from agent_runtime.application.write_scope import WriteRefused, is_inside as _inside

from fs_tools import _resolve_write  # built-in 'core_fs' bundle


class _Cfg:
    def __init__(self, workspace):
        self.workspace = str(workspace)


@pytest.fixture
def scoped(tmp_path):
    """A run whose agent may write under agents/, except its own directory."""
    agents = tmp_path / "agents"
    (agents / "note-taker").mkdir(parents=True)
    (agents / "agent-builder").mkdir(parents=True)
    (tmp_path / "plugins").mkdir()
    set_run_context(
        RunContext(
            agent_id="agent-builder",
            session_key="s",
            mode="interactive",
            workspace=str(agents / "agent-builder" / "workspace"),
            write_roots=(str(agents),),
            write_denies=(str(agents / "agent-builder"),),
        )
    )
    yield tmp_path, _Cfg(agents / "agent-builder" / "workspace")
    set_run_context(None)


def _refusal(cfg, path) -> str:
    with pytest.raises(WriteRefused) as e:
        _resolve_write(cfg, str(path))
    return str(e.value)


# ── inside the roots ────────────────────────────────────────────────────────
def test_it_may_write_into_an_agent_it_is_authoring(scoped):
    root, cfg = scoped
    target = root / "agents" / "note-taker" / "agent.toml"
    assert _resolve_write(cfg, str(target)) == target


def test_it_may_create_a_new_agent_directory(scoped):
    """A root, not a list of agent ids — an agent that does not exist yet is already covered."""
    root, cfg = scoped
    target = root / "agents" / "built-tomorrow" / "IDENTITY.md"
    assert _resolve_write(cfg, str(target)) == target


# ── outside the roots ───────────────────────────────────────────────────────
def test_the_shared_plugins_dir_is_refused(scoped):
    """THE one that matters: a tool written here is never sandboxed on a buyer's machine."""
    root, cfg = scoped
    msg = _refusal(cfg, root / "plugins" / "sneaky" / "sneaky.py")
    assert "outside this agent's write scope" in msg


def test_anywhere_else_on_disk_is_refused(scoped):
    root, cfg = scoped
    assert "outside this agent's write scope" in _refusal(cfg, root / "elsewhere.txt")


def test_dot_dot_cannot_climb_out(scoped):
    """Without realpath, `agents/../plugins/x` reads as inside the root."""
    root, cfg = scoped
    escape = root / "agents" / ".." / "plugins" / "x.py"
    assert "outside this agent's write scope" in _refusal(cfg, escape)


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need admin on Windows")
def test_a_symlink_out_of_the_root_cannot_be_used(scoped):
    root, cfg = scoped
    link = root / "agents" / "escape"
    os.symlink(root / "plugins", link)
    assert "outside this agent's write scope" in _refusal(cfg, link / "x.py")


# ── deny beats allow ────────────────────────────────────────────────────────
def test_it_cannot_write_its_own_definition(scoped):
    """The rules it runs under. Inside the root, and still refused."""
    root, cfg = scoped
    for name in ("agent.toml", "AGENTS.md", "skills/build-agent/SKILL.md"):
        msg = _refusal(cfg, root / "agents" / "agent-builder" / name)
        assert "denied" in msg


def test_it_cannot_write_its_own_workspace(scoped):
    """Nothing it writes should be able to reach its own constraints — including by leaving
    itself a note that later gets read back as instructions."""
    root, cfg = scoped
    assert "denied" in _refusal(cfg, root / "agents" / "agent-builder" / "workspace" / "n.md")


def test_a_relative_path_resolves_into_the_workspace_and_is_then_denied(scoped):
    """Relative paths still resolve against the workspace as they always did; the scope is
    applied to the RESULT, so a relative path cannot dodge it."""
    _root, cfg = scoped
    assert "denied" in _refusal(cfg, "notes.md")


# ── the refusal has to be actionable ────────────────────────────────────────
def test_the_refusal_says_what_to_do_instead(scoped):
    root, cfg = scoped
    msg = _refusal(cfg, root / "plugins" / "x" / "x.py")
    assert "that agent's own directory" in msg, "name the legitimate path"
    assert "USER's decision" in msg, "a shared tool is not its call to make"
    assert "exec" in msg, "say not to route around it, since exec is not covered by this block"


# ── an agent that declares nothing is untouched ─────────────────────────────
def test_no_roots_means_unrestricted(tmp_path):
    set_run_context(
        RunContext(agent_id="plain", session_key="s", mode="interactive",
                   workspace=str(tmp_path))
    )
    try:
        target = tmp_path / "anything" / "at" / "all.txt"
        assert _resolve_write(_Cfg(tmp_path), str(target)) == target
    finally:
        set_run_context(None)


def test_no_run_context_at_all_is_unrestricted(tmp_path):
    """A tool invoked outside a run (a CLI one-shot, a test) has no context to consult."""
    set_run_context(None)
    target = tmp_path / "x.txt"
    assert _resolve_write(_Cfg(tmp_path), str(target)) == target


# ── containment itself ──────────────────────────────────────────────────────
def test_a_path_equal_to_the_root_is_inside_it(tmp_path):
    assert _inside(tmp_path, str(tmp_path))


def test_a_sibling_with_a_shared_prefix_is_not_inside(tmp_path):
    """`/x/agents-backup` must not count as inside `/x/agents` — a plain startswith says it is."""
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents-backup").mkdir()
    assert not _inside(tmp_path / "agents-backup" / "f.txt", str(tmp_path / "agents"))


# ── the token expansion ─────────────────────────────────────────────────────
class _Agent:
    def __init__(self, d):
        self.dir = d
        self.write_roots = ("<agents_dir>",)
        self.write_denies = ("<agent_dir>",)


def test_tokens_expand_to_this_installs_real_paths(tmp_path):
    """agent.toml cannot hardcode a path: agents/ is <repo>/v2/agents/ in a checkout and
    ~/.agentd/agents/ on an install."""
    agent_dir = tmp_path / "agents" / "agent-builder"
    agent_dir.mkdir(parents=True)
    a = _Agent(agent_dir)
    assert AgentService._expand_paths(a, a.write_roots) == (str(tmp_path / "agents"),)
    assert AgentService._expand_paths(a, a.write_denies) == (str(agent_dir),)


def test_an_unknown_token_is_dropped_rather_than_taken_literally(tmp_path):
    """A typo must NARROW the scope, never widen it. Kept as a literal, `<typo>/x` would be a
    root that matches nothing — harmless — but dropping it is unambiguous."""
    agent_dir = tmp_path / "agents" / "x"
    agent_dir.mkdir(parents=True)
    assert AgentService._expand_paths(_Agent(agent_dir), ("<typoo_dir>/x",)) == ()


def test_an_agent_with_no_dir_expands_nothing(tmp_path):
    """A spec built without a directory (tests, the bootstrap 'main') has no anchor for the
    tokens. Expanding to something wrong would be worse than expanding to nothing."""
    class _NoDir:
        dir = None
    assert AgentService._expand_paths(_NoDir(), ("<agents_dir>",)) == ()


# ── the real agent-builder declaration ──────────────────────────────────────
def test_agent_builder_declares_a_scope_that_excludes_plugins_and_itself():
    import tomllib

    root = Path(__file__).resolve().parents[2]
    raw = tomllib.loads((root / "agents" / "agent-builder" / "agent.toml").read_text("utf-8"))
    fs = (raw.get("tools") or {}).get("fs") or {}
    assert fs.get("write_roots") == ["<agents_dir>"], "agents/ and nothing wider"
    assert fs.get("deny") == ["<agent_dir>"], "it must not edit the rules it runs under"


def test_an_agent_object_without_the_fields_is_unrestricted():
    """The service is handed stand-in agent objects as well as real specs (tests, and any
    caller building a minimal one). A missing field must read as "declared nothing" — reaching
    for it directly crashed a whole run and swallowed the tool call with it."""
    import types

    a = types.SimpleNamespace(dir=None)
    assert AgentService._expand_paths(a, getattr(a, "write_roots", ())) == ()


# ── item 2: an INSTALLED agent is not yours to edit ─────────────────────────
# Trust comes from the marketplace ledger, the same record the sandbox classifier reads. Editing
# a downloaded agent leaves it no longer matching what its publisher shipped while still carrying
# their name — and its provenance record then describes something that is not on disk.
@pytest.fixture
def with_installed(tmp_path):
    agents = tmp_path / "agents"
    for a in ("mine", "downloaded", "agent-builder"):
        (agents / a).mkdir(parents=True)
    set_run_context(
        RunContext(
            agent_id="agent-builder", session_key="s", mode="interactive",
            workspace=str(agents / "agent-builder" / "workspace"),
            write_roots=(str(agents),),
            write_denies=(str(agents / "agent-builder"),),
            protected_paths=(str(agents / "downloaded"),),
        )
    )
    yield tmp_path, _Cfg(agents / "agent-builder" / "workspace")
    set_run_context(None)


def test_an_installed_agent_cannot_be_edited(with_installed):
    root, cfg = with_installed
    msg = _refusal(cfg, root / "agents" / "downloaded" / "agent.toml")
    assert "INSTALLED from a package" in msg
    assert "build your own agent" in msg, "name the legitimate alternative"


def test_a_locally_authored_agent_is_still_writable(with_installed):
    root, cfg = with_installed
    target = root / "agents" / "mine" / "agent.toml"
    assert _resolve_write(cfg, str(target)) == target


def test_protection_applies_even_with_no_declared_roots(tmp_path):
    """This is a PLATFORM rule, not a scope its author opted into. An agent that declares no
    write_roots is otherwise unrestricted — it must still not edit someone else's package."""
    agents = tmp_path / "agents"
    (agents / "downloaded").mkdir(parents=True)
    set_run_context(
        RunContext(agent_id="anyone", session_key="s", mode="interactive",
                   workspace=str(tmp_path),
                   protected_paths=(str(agents / "downloaded"),))
    )
    try:
        with pytest.raises(WriteRefused, match="INSTALLED from a package"):
            _resolve_write(_Cfg(tmp_path), str(agents / "downloaded" / "x.md"))
    finally:
        set_run_context(None)


# ── how the protected set is derived ────────────────────────────────────────
class _AgentAt:
    write_roots = ()
    write_denies = ()

    def __init__(self, d):
        self.dir = d


def _svc(installed):
    return AgentService(
        engine=None, tools=[], registry=None, make_session=None, build_prompt=None,
        installed_agents=(lambda: installed),
    )


def test_only_installed_agents_are_protected(tmp_path):
    agents = tmp_path / "agents"
    a = _AgentAt(agents / "agent-builder")
    got = _svc(frozenset({"downloaded", "another"}))._protected_paths(a)
    assert got == (str(agents / "another"), str(agents / "downloaded"))


def test_nothing_installed_protects_nothing(tmp_path):
    a = _AgentAt(tmp_path / "agents" / "agent-builder")
    assert _svc(frozenset())._protected_paths(a) == ()


def test_an_unreadable_ledger_protects_everything(tmp_path):
    """FAIL CLOSED. `None` means the ledger could not be read — which is not the same as
    "nothing is installed". We do not know which agents are someone else's, and permitting the
    write anyway would be a fallback that hides the failure. Authoring stops until it is fixed,
    loudly, which is the intended trade."""
    agents = tmp_path / "agents"
    a = _AgentAt(agents / "agent-builder")
    assert _svc(None)._protected_paths(a) == (str(agents),)


def test_no_ledger_callable_at_all_protects_nothing(tmp_path):
    """A service built without the injection (tests, an older composition root) behaves as
    before rather than refusing every write."""
    svc = AgentService(engine=None, tools=[], registry=None, make_session=None, build_prompt=None)
    assert svc._protected_paths(_AgentAt(tmp_path / "agents" / "x")) == ()


# ── the block and the documentation have to agree ───────────────────────────
# A wall nobody was told about is a wall you hit, guess at, and route around. These check that
# what the code enforces is what the agent was told — not the wording, the SUBSTANCE.
BUILDER = Path(__file__).resolve().parents[2] / "agents" / "agent-builder"


def test_the_skill_states_the_scope_it_will_be_held_to():
    text = (BUILDER / "skills" / "build-agent" / "SKILL.md").read_text("utf-8").lower()
    assert "where you may write" in text
    for forbidden in ("plugins", "installed"):
        assert forbidden in text, f"the skill never mentions {forbidden}"
    assert "reading is not restricted" in text, (
        "otherwise it will assume reads are scoped too and stop reading its own skill"
    )
    assert "exec" in text, "say that exec is not a way around it"


def test_the_standing_rules_say_it_too():
    """The skill is read while authoring; AGENTS.md is present on every turn. A boundary this
    absolute belongs in both."""
    text = (BUILDER / "AGENTS.md").read_text("utf-8").lower()
    assert "only write inside the agent you are building" in text
    assert "exec" in text


def test_the_skill_no_longer_claims_write_is_unsandboxed():
    """It said `write` is not sandboxed. That was true when written and is now false — exactly
    the drift that makes a document worse than no document."""
    text = (BUILDER / "skills" / "build-agent" / "SKILL.md").read_text("utf-8")
    assert "`write` is not sandboxed" not in text


def test_agent_builder_would_actually_be_refused_the_things_the_skill_lists(tmp_path):
    """The documentation claims four things are refused. Run the real check and confirm."""
    agents = tmp_path / "agents"
    (agents / "agent-builder").mkdir(parents=True)
    (agents / "downloaded").mkdir(parents=True)
    (tmp_path / "plugins").mkdir()
    set_run_context(
        RunContext(
            agent_id="agent-builder", session_key="s", mode="interactive",
            workspace=str(agents / "agent-builder" / "workspace"),
            write_roots=(str(agents),),
            write_denies=(str(agents / "agent-builder"),),
            protected_paths=(str(agents / "downloaded"),),
        )
    )
    try:
        cfg = _Cfg(agents / "agent-builder" / "workspace")
        for claimed in (
            tmp_path / "plugins" / "x" / "x.py",
            agents / "agent-builder" / "agent.toml",
            agents / "agent-builder" / "workspace" / "note.md",
            agents / "downloaded" / "agent.toml",
            tmp_path / "somewhere" / "else.txt",
        ):
            with pytest.raises(WriteRefused):
                _resolve_write(cfg, str(claimed))
    finally:
        set_run_context(None)

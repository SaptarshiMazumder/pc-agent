"""Building an agent's window — the step that used to need a terminal.

THE FAILURE THIS EXISTS TO END. A React agent keeps source in `app/` and built output in `ui/`,
and the daemon serves only `ui/`. So editing `app/src/App.tsx` changes nothing anyone can see
until vite runs, and until this tool the only way to run vite was a shell — which the people who
INSTALL the product do not have. They would edit the source, reload the window, get the old
screen, and find nothing anywhere saying why.

Everything here is driven through fakes rather than a real Node: the point under test is the
ORDER and the REPORTING, not whether vite works.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_authoring.application.build_app_service import BuildAppError, BuildAppService
from agent_authoring.infrastructure.node_toolchain import CommandOutput, NodeMissing, NodeToolchain


class FakeReader:
    def __init__(self, root: Path):
        self._root = root

    def agent_dir(self, agent_id: str):
        d = self._root / agent_id
        return d if d.is_dir() else None

    def known_ids(self):
        return sorted(p.name for p in self._root.iterdir() if p.is_dir())


class FakeToolchain:
    """Records what it was asked to run, and answers from a script."""

    def __init__(self, result: CommandOutput | None = None, missing: bool = False):
        self.calls: list[tuple[list[str], Path]] = []
        self._result = result or CommandOutput(ok=True, output="built in 500ms")
        self._missing = missing

    def require(self):
        if self._missing:
            raise NodeMissing("no Node.js available")
        return "/fake/node"

    def npm(self, args, cwd, timeout=600.0):
        self.calls.append((args, cwd))
        return self._result


class FakeStore:
    def __init__(self, ok: bool = True, how: str = "linked", detail: str = ""):
        from agent_authoring.infrastructure.app_dependency_store import DependencyOutcome

        self.outcome = DependencyOutcome(ok, how, detail)
        self.asked: list[Path] = []

    def ensure(self, app_dir: Path):
        self.asked.append(app_dir)
        return self.outcome


@pytest.fixture
def agent(tmp_path):
    """An agent with a React app, and a `ui/` the fake build will appear to have written."""
    app = tmp_path / "demo" / "app"
    app.mkdir(parents=True)
    (app / "package.json").write_text('{"name":"agent-app"}', encoding="utf-8")
    ui = tmp_path / "demo" / "ui"
    ui.mkdir()
    (ui / "index.html").write_text("<html></html>", encoding="utf-8")
    return tmp_path


def service(root, toolchain=None, store=None):
    return BuildAppService(FakeReader(root), toolchain or FakeToolchain(), store or FakeStore())


# ── the happy path ──────────────────────────────────────────────────────────
def test_it_runs_the_build_and_reports_what_landed(agent):
    tools = FakeToolchain()
    result = service(agent, tools).build("demo")

    assert result.ok
    assert tools.calls == [(["run", "build"], agent / "demo" / "app")]
    assert "index.html" in result.written


def test_dependencies_are_provided_before_the_build_runs(agent):
    """Order matters and is invisible in the output: a build with no node_modules fails with a
    resolver error that names a package rather than the missing step."""
    store = FakeStore()
    service(agent, store=store).build("demo")
    assert store.asked == [agent / "demo" / "app"]


def test_how_the_dependencies_arrived_is_reported(agent):
    """'linked' and 'installed' are minutes and megabytes apart, and the caller should be able to
    say which happened rather than guess."""
    assert service(agent, store=FakeStore(how="installed")).build("demo").dependencies == "installed"


# ── refusals, each with something the user can act on ───────────────────────
def test_an_unknown_agent_names_the_ones_that_exist(agent):
    with pytest.raises(BuildAppError) as e:
        service(agent).build("nope")
    assert "demo" in str(e.value)


def test_an_agent_with_no_app_is_told_it_needs_no_build(agent):
    """A hand-written ui/ is served straight off disk. Reporting 'build failed' for one would send
    the author looking for a broken toolchain instead of telling them there is nothing to do."""
    (agent / "vanilla").mkdir()
    with pytest.raises(BuildAppError) as e:
        service(agent).build("vanilla")
    assert "no app/" in str(e.value)
    assert "straight off disk" in str(e.value)


def test_no_node_says_so_rather_than_failing_at_npm(agent):
    with pytest.raises(BuildAppError) as e:
        service(agent, FakeToolchain(missing=True)).build("demo")
    assert "Node" in str(e.value)


def test_dependencies_that_could_not_be_provided_stop_the_build(agent):
    tools = FakeToolchain()
    store = FakeStore(ok=False, how="failed", detail="ENOTFOUND registry.npmjs.org")
    with pytest.raises(BuildAppError) as e:
        service(agent, tools, store).build("demo")

    assert "ENOTFOUND" in str(e.value), "the real reason must survive"
    assert tools.calls == [], "a build with no dependencies must not be attempted"


# ── failure reporting: the whole reason to use this rather than a shell ─────
def test_a_failed_build_returns_VITE_S_OWN_ERROR(agent):
    """A vite error names the file and the line. Replacing it with a verdict turns a one-line fix
    into a hunt, and the model reading this has no other way to see it."""
    broken = CommandOutput(ok=False, output="src/App.tsx:12:3 - error TS2304: Cannot find name 'x'")
    with pytest.raises(BuildAppError) as e:
        service(agent, FakeToolchain(broken)).build("demo")

    assert "src/App.tsx:12:3" in str(e.value)
    assert "TS2304" in str(e.value)


def test_a_timeout_says_so_and_keeps_the_partial_output(agent):
    slow = CommandOutput(ok=False, output="transforming...", timed_out=True)
    with pytest.raises(BuildAppError) as e:
        service(agent, FakeToolchain(slow)).build("demo")

    assert "stopped" in str(e.value)
    assert "transforming" in str(e.value)


def test_a_build_that_succeeds_but_writes_NOTHING_is_a_failure(agent):
    """The command exited 0 and produced no ui/, which means it is not the build we think it is —
    an outDir pointing elsewhere, or a `build` script that only lints. Reporting success here
    sends the user to look for a change that was never written."""
    empty = agent / "empty"
    (empty / "app").mkdir(parents=True)
    (empty / "app" / "package.json").write_text("{}", encoding="utf-8")

    with pytest.raises(BuildAppError) as e:
        service(agent).build("empty")
    assert "wrote nothing" in str(e.value)
    assert "outDir" in str(e.value), "it must name the setting that is usually wrong"


# ── finding the toolchain ───────────────────────────────────────────────────
def test_the_bundled_node_wins_over_one_on_the_path(tmp_path):
    """A user's own Node may be years old, and 'builds for the author, not the user' is the exact
    class of failure the bundle exists to end."""
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    (bundled / "node.exe").write_text("", encoding="utf-8")

    found = NodeToolchain(env={"AGENTD_NODE_DIR": str(bundled), "PATH": ""}).node_dir()
    assert found == str(bundled)


def test_a_bundled_path_with_no_node_in_it_is_ignored(tmp_path):
    """Fails over to PATH rather than trusting the variable. An empty directory would otherwise
    make every build fail with 'no such file' instead of using the Node that is right there."""
    empty = tmp_path / "empty"
    empty.mkdir()
    assert NodeToolchain(env={"AGENTD_NODE_DIR": str(empty), "PATH": ""}).node_dir() == ""


def test_the_message_for_no_node_tells_the_user_what_to_do():
    with pytest.raises(NodeMissing) as e:
        NodeToolchain(env={"PATH": ""}).require()
    assert "Node 18+" in str(e.value)

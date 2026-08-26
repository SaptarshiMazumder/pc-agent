"""Build-on-write: the mechanism that replaced "remember to run build_app".

`app/` is source and `ui/` is what the daemon serves, so an edit nobody compiled is invisible —
the user reloads, sees the old screen, and every file they can inspect says the work was done.
The observer turns every successful write inside an agent's `app/` into a debounced build, and a
successful build into the `app.rebuilt` the open window reloads on. The loop the hot-reload work
exists for, with no step left to forget.
"""

import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "agents/agent-builder/plugins/agent-authoring")
)

from agent_authoring.application.app_auto_build_observer import AppAutoBuildObserver
from agent_runtime.application.interfaces.run_observer import ToolEvent


class _Registry:
    def __init__(self, root: Path, *ids: str):
        self._root = root
        self._ids = list(ids)
        for aid in ids:
            (root / aid / "app" / "src").mkdir(parents=True, exist_ok=True)

    def list_ids(self):
        return self._ids

    def resolve_dir(self, agent_id):
        d = self._root / agent_id
        return d if d.is_dir() else None


class _Builder:
    def __init__(self, fail: bool = False):
        self.built: list[str] = []
        self._fail = fail
        self.done = threading.Event()

    def build(self, agent_id: str):
        if self._fail:
            self.done.set()
            raise RuntimeError("vite: src/App.tsx(3,1): '}' expected")
        self.built.append(agent_id)
        self.done.set()


def _write(path: Path, is_error: bool = False) -> ToolEvent:
    return ToolEvent("write", {"path": str(path)}, "after", is_error=is_error)


def _observer(reg, builder, announce=None):
    # A short debounce so the suite is not sitting out real seconds.
    return AppAutoBuildObserver(reg, builder, announce=announce, debounce_s=0.05)


def test_a_write_inside_app_builds_that_agent(tmp_path):
    reg = _Registry(tmp_path, "recipe-box")
    builder = _Builder()
    obs = _observer(reg, builder)

    obs.on_tool(_write(tmp_path / "recipe-box" / "app" / "src" / "App.tsx"))

    assert builder.done.wait(2), "the build never ran"
    assert builder.built == ["recipe-box"]


def test_a_burst_of_writes_is_one_build(tmp_path):
    """The model writes ten files in one turn. Ten vite builds would take most of a minute and
    nine of them would be of half-finished states."""
    reg = _Registry(tmp_path, "recipe-box")
    builder = _Builder()
    obs = _observer(reg, builder)

    for name in ("App.tsx", "store.ts", "styles.css", "Sidebar.tsx"):
        obs.on_tool(_write(tmp_path / "recipe-box" / "app" / "src" / name))

    assert builder.done.wait(2)
    time.sleep(0.15)  # long enough for any stray extra timer to have fired
    assert builder.built == ["recipe-box"], "the burst was not folded into one build"


def test_writes_outside_app_build_nothing(tmp_path):
    """agent.toml, IDENTITY.md, skills/ — none of it is compiled, and a build per edit to any of
    them would be pure noise."""
    reg = _Registry(tmp_path, "recipe-box")
    builder = _Builder()
    obs = _observer(reg, builder)

    obs.on_tool(_write(tmp_path / "recipe-box" / "agent.toml"))
    obs.on_tool(_write(tmp_path / "recipe-box" / "skills" / "cook" / "SKILL.md"))
    obs.on_tool(_write(tmp_path / "somewhere-else" / "app" / "notes.txt"))

    time.sleep(0.15)
    assert builder.built == []


def test_a_failed_write_builds_nothing(tmp_path):
    """A refused edit changed no file, so there is nothing new to compile."""
    reg = _Registry(tmp_path, "recipe-box")
    builder = _Builder()
    obs = _observer(reg, builder)

    obs.on_tool(_write(tmp_path / "recipe-box" / "app" / "src" / "App.tsx", is_error=True))

    time.sleep(0.15)
    assert builder.built == []


def test_a_read_builds_nothing(tmp_path):
    reg = _Registry(tmp_path, "recipe-box")
    builder = _Builder()
    obs = _observer(reg, builder)

    obs.on_tool(ToolEvent("read", {"path": str(tmp_path / "recipe-box" / "app" / "x.ts")}, "after"))
    obs.on_tool(ToolEvent("write", {"path": str(tmp_path / "recipe-box" / "app" / "x.ts")}, "before"))

    time.sleep(0.15)
    assert builder.built == []


def test_a_FAILED_build_announces_nothing(tmp_path):
    """The sibling rule, kept: a failed build leaves ui/ untouched, so a reload would repaint the
    old screen and read as "my change did nothing" when it actually did not compile."""
    reg = _Registry(tmp_path, "recipe-box")
    builder = _Builder(fail=True)
    announced: list[str] = []
    obs = _observer(reg, builder, announce=announced.append)
    # Pretend a loop was captured; the announce must still not fire on failure.
    obs._loop = SimpleNamespace(call_soon_threadsafe=lambda fn, *a: fn(*a))

    obs.on_tool(_write(tmp_path / "recipe-box" / "app" / "src" / "App.tsx"))

    assert builder.done.wait(2)
    time.sleep(0.05)
    assert announced == []


def test_a_successful_build_announces_the_agent(tmp_path):
    reg = _Registry(tmp_path, "recipe-box")
    builder = _Builder()
    announced: list[str] = []
    obs = _observer(reg, builder, announce=announced.append)
    obs._loop = SimpleNamespace(call_soon_threadsafe=lambda fn, *a: fn(*a))

    obs.on_tool(_write(tmp_path / "recipe-box" / "app" / "src" / "App.tsx"))

    assert builder.done.wait(2)
    time.sleep(0.05)
    assert announced == ["recipe-box"]


def test_it_never_halts_the_run(tmp_path):
    """An observer's return value is a HALT REASON. Building is a side effect; a window that
    stopped the model's run to compile itself would be the tail wagging the dog."""
    reg = _Registry(tmp_path, "recipe-box")
    obs = _observer(reg, _Builder())
    assert obs.on_tool(_write(tmp_path / "recipe-box" / "app" / "src" / "App.tsx")) is None
    assert obs.on_turn(3) is None
    assert obs.reset() is None

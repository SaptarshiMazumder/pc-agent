"""Per-agent workspace isolation: the file/exec tools resolve relative paths against
the CURRENT run's workspace (the agent's own dir), falling back to the global config
workspace when unset (which keeps `main` on the shared legacy workspace)."""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.application import run_context as rc
from agentd.application.run_context import RunContext, current_workspace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "core_fs"))
from fs_tools import _resolve  # built-in 'core_fs' bundle


def test_current_workspace_default_when_no_context():
    assert current_workspace("/global") == "/global"


def test_current_workspace_uses_run_context():
    tok = rc._current.set(RunContext("scout", "s", "interactive", workspace="/a/scout/ws"))
    try:
        assert current_workspace("/global") == "/a/scout/ws"
    finally:
        rc._current.reset(tok)


def test_empty_workspace_falls_back_to_global():
    # main carries workspace="" -> stays on the global/legacy workspace (back-compat)
    tok = rc._current.set(RunContext("main", "s", "interactive", workspace=""))
    try:
        assert current_workspace("/global") == "/global"
    finally:
        rc._current.reset(tok)


def test_resolve_relative_path_roots_at_run_workspace():
    cfg = SimpleNamespace(workspace=Path("/global"))
    tok = rc._current.set(
        RunContext("scout", "s", "interactive", workspace=str(Path("/a/scout/ws")))
    )
    try:
        assert _resolve(cfg, "notes.txt") == Path("/a/scout/ws") / "notes.txt"
    finally:
        rc._current.reset(tok)


def test_resolve_relative_path_falls_back_to_global_for_main():
    cfg = SimpleNamespace(workspace=Path("/global"))
    tok = rc._current.set(RunContext("main", "s", "interactive", workspace=""))
    try:
        assert _resolve(cfg, "notes.txt") == Path("/global") / "notes.txt"
    finally:
        rc._current.reset(tok)


def test_resolve_absolute_path_left_untouched():
    cfg = SimpleNamespace(workspace=Path("/global"))
    tok = rc._current.set(RunContext("scout", "s", "interactive", workspace="/a/scout/ws"))
    try:
        abs_path = "C:/abs/file.txt" if sys.platform.startswith("win") else "/etc/hosts"
        assert _resolve(cfg, abs_path) == Path(abs_path)
    finally:
        rc._current.reset(tok)

"""Workspace scratch + cleanup: <workspace>/tmp/ is a sanctioned throwaway dir (auto-swept
by age, never indexed); cleanup() removes scratch with a dry-run preview; home is never touched."""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.infrastructure.workspace.cleanup import (
    SCRATCH_DIRNAME,
    cleanup,
    plan_cleanup,
    sweep_scratch,
)


def _ws(tmp_path):
    ws = tmp_path / "ws"
    (ws / SCRATCH_DIRNAME).mkdir(parents=True)
    return ws


def test_sweep_scratch_deletes_old_keeps_new(tmp_path):
    ws = _ws(tmp_path)
    old = ws / "tmp" / "old.txt"
    old.write_text("x", encoding="utf-8")
    new = ws / "tmp" / "new.txt"
    new.write_text("y", encoding="utf-8")
    past = time.time() - 48 * 3600
    os.utime(old, (past, past))  # age old.txt to 48h
    assert sweep_scratch(ws, ttl_hours=24) == 1
    assert not old.exists() and new.exists()  # only the aged one swept


def test_sweep_scratch_noop_when_disabled_or_absent(tmp_path):
    ws = _ws(tmp_path)
    f = ws / "tmp" / "a.txt"
    f.write_text("x", encoding="utf-8")
    os.utime(f, (time.time() - 99 * 3600,) * 2)
    assert sweep_scratch(ws, ttl_hours=0) == 0 and f.exists()  # ttl<=0 -> off
    assert sweep_scratch(tmp_path / "no-ws", ttl_hours=1) == 0  # no tmp/ dir


def test_plan_and_cleanup_tmp_and_patterns(tmp_path):
    ws = _ws(tmp_path)
    (ws / "tmp" / "junk.bin").write_text("j", encoding="utf-8")
    (ws / "deliverable.xlsx").write_text("d", encoding="utf-8")
    (ws / "tmp_scratch.txt").write_text("s", encoding="utf-8")  # matches pattern, OUTSIDE tmp/
    plan = plan_cleanup(ws, patterns=("tmp_*",))
    assert "tmp/junk.bin" in plan and "tmp_scratch.txt" in plan
    assert "deliverable.xlsx" not in plan  # durable -> untouched
    # dry-run deletes nothing
    assert cleanup(ws, patterns=("tmp_*",), dry_run=True) == plan
    assert (ws / "tmp" / "junk.bin").exists()
    # real cleanup removes the targets, keeps the deliverable
    deleted = cleanup(ws, patterns=("tmp_*",))
    assert set(deleted) == set(plan)
    assert not (ws / "tmp" / "junk.bin").exists()
    assert not (ws / "tmp_scratch.txt").exists()
    assert (ws / "deliverable.xlsx").exists()


def test_cleanup_refuses_home():
    # the home directory is never tidied (defensive — agents never have it as a workspace now)
    assert plan_cleanup(Path.home()) == []
    assert cleanup(Path.home()) == []
    assert sweep_scratch(Path.home(), ttl_hours=1) == 0

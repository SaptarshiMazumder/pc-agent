"""RUN seam: resolve_run_outcome folds the agent's declared outcome into the run status,
and (when enforced) marks a cron run that finished `ok` but declared NOTHING as
`incomplete` — so a scheduled run can't be logged as a silent success it never verified."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.domain.autonomy import resolve_run_outcome


def test_declared_done_maps_to_ok():
    assert resolve_run_outcome("ok", ("done", "all good"), enforce=True, is_cron=True) \
        == ("ok", "done", "all good")


def test_declared_failed_maps_to_failed():
    assert resolve_run_outcome("ok", ("failed", "drive auth"), enforce=True, is_cron=True) \
        == ("failed", "failed", "drive auth")


def test_declared_blocked_maps_to_blocked():
    assert resolve_run_outcome("ok", ("blocked", "need creds"), enforce=True, is_cron=True)[0] == "blocked"


def test_cron_ok_without_declaration_is_incomplete():
    status, outcome, detail = resolve_run_outcome("ok", None, enforce=True, is_cron=True)
    assert status == "incomplete" and outcome is None and detail


def test_enforce_off_keeps_ok():
    assert resolve_run_outcome("ok", None, enforce=False, is_cron=True)[0] == "ok"


def test_non_cron_run_not_enforced():
    # interactive/heartbeat runs (no cron_run_id) are seen live -> not forced incomplete
    assert resolve_run_outcome("ok", None, enforce=True, is_cron=False)[0] == "ok"


def test_engine_error_wins_over_missing_declaration():
    assert resolve_run_outcome("error", None, enforce=True, is_cron=True)[0] == "error"


def test_aborted_preserved():
    assert resolve_run_outcome("aborted", None, enforce=True, is_cron=True)[0] == "aborted"

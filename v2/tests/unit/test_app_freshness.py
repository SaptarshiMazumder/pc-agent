"""A window built from source that no longer exists.

`app/` is compiled into `ui/`. The daemon serves `ui/`, the packer takes `ui/`, the marketplace
ships `ui/` — nothing reads `app/src`. So an agent whose source has moved on from its build looks
finished from every angle its author can check (the source is right, the tests pass, the files are
there) and hands everyone else last week's screen.

This became the EASY mistake the moment building stopped being part of editing. With a terminal,
`npm run build` was the thing you were already doing; with `build_app` it is a separate step, and
separate steps get skipped.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_authoring.domain.freshness_rules import TOLERANCE_S, FreshnessRules
from agent_authoring.domain.rulebook import PACK, PUBLISH, blockers

RULES = FreshnessRules()

BUILT_APP = ["app/package.json", "app/src/App.tsx", "ui/index.html", "ui/assets/index-abc.js"]


def check(mtimes: dict, files=None):
    return {f.code: f for f in RULES.check(None, {}, files or BUILT_APP, mtimes)}


def times(source: float, output: float) -> dict:
    return {"app/package.json": 0, "app/src/App.tsx": source, "ui/index.html": output}


# ── the failure ─────────────────────────────────────────────────────────────
def test_source_newer_than_the_build_is_refused():
    found = check(times(source=5_000, output=1_000))

    assert "APP_BUILD_STALE" in found
    assert found["APP_BUILD_STALE"].level == "error"
    assert "build_app" in found["APP_BUILD_STALE"].fix


def test_it_names_the_file_that_moved_and_by_how_much():
    """'ui/ is stale' sends the author looking. Naming the newer file and the gap turns it into
    one glance — and the SIZE is what distinguishes a forgotten build from a just-saved file."""
    found = check(times(source=400_000, output=1_000))
    message = found["APP_BUILD_STALE"].message

    assert "app/src/App.tsx" in message
    assert "4 days" in message, f"a four-day gap should read in days, got: {message}"


def test_a_small_gap_reads_in_small_units():
    """The same sentence has to serve both ends: 'a few seconds' is somebody who just saved, 'four
    days' is a build nobody has run since. One unit for both would make one of them unreadable."""
    assert "5m" in check(times(source=1_300, output=1_000))["APP_BUILD_STALE"].message


def test_it_blocks_packing_and_publishing():
    """The whole point. A stale build that only WARNS is a stale build that ships."""
    assert "APP_BUILD_STALE" in blockers(PACK)
    assert "APP_BUILD_STALE" in blockers(PUBLISH)


def test_config_counts_as_source():
    """An edited vite.config.ts moves where the build WRITES; an edited package.json moves what it
    builds with. Either one leaves the existing ui/ built by different rules."""
    found = check(
        {"app/package.json": 0, "app/vite.config.ts": 5_000, "ui/index.html": 1_000},
        files=["app/package.json", "app/vite.config.ts", "ui/index.html"],
    )
    assert "APP_BUILD_STALE" in found


# ── staying quiet, which is what makes it believable ────────────────────────
def test_a_fresh_build_says_nothing():
    assert not check(times(source=1_000, output=5_000))


def test_a_hand_written_window_has_nothing_to_be_stale():
    """No app/ means no build step. The window is served straight off disk and is live the moment
    it is saved — reporting staleness would be reporting a step that does not exist."""
    assert not check(
        {"ui/app.js": 9_000, "ui/index.html": 1_000},
        files=["ui/app.js", "ui/index.html"],
    )


def test_an_app_that_was_never_built_is_not_this_rule_s_business():
    """Missing ui/ entirely is somebody mid-scaffold, and the layout rules say something more
    useful about it than 'your build is old'."""
    assert not check(
        {"app/package.json": 0, "app/src/App.tsx": 5_000},
        files=["app/package.json", "app/src/App.tsx"],
    )


def test_a_gap_inside_the_tolerance_is_not_a_finding():
    """Filesystems, archives and copies disagree about sub-second times, and a fresh checkout can
    land source and output in the same tick. Firing there would make the rule noise on day one."""
    assert not check(times(source=1_000 + TOLERANCE_S - 0.5, output=1_000))


def test_no_timestamps_at_all_disables_the_check():
    """What a caller that could not stat should get. A gate built on a failed stat call is a gate
    that refuses to ship because of a permissions hiccup."""
    assert not check({})

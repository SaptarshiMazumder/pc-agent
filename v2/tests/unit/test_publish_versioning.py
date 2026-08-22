"""Publishing raises the version itself, and never guesses one.

INSTALLS SUPERSEDE BY VERSION. Republishing the same number reaches nobody — the service answers
409, and anyone who already has that number is never offered the new build. The advice used to be
"bump `version` in agent.toml", a manual step remembered by nobody and discovered only after a
full pack-and-upload had already run.

Two things it must not do while fixing that: guess a successor for a version it cannot parse, and
destroy the file it edits. These agent.toml files are mostly comments — the reasoning for every
declaration lives in them — so the write is a targeted line edit rather than a re-serialise.
"""

from __future__ import annotations

import pytest

from agent_authoring.domain.versioning import (
    VersionError,
    next_version,
    resolve_version,
    rewrite_version,
)

# --------------------------------------------------------------------------- bumping


@pytest.mark.parametrize(
    "current,part,expected",
    [
        ("1.0.0", "patch", "1.0.1"),
        ("1.0.9", "patch", "1.0.10"),
        ("1.2.3", "minor", "1.3.0"),
        ("1.2.3", "major", "2.0.0"),
    ],
)
def test_a_bump_resets_the_lower_parts(current, part, expected):
    """The whole point of the scheme: a new minor starts at patch zero rather than carrying the
    old patch count forward."""
    assert next_version(current, part) == expected


@pytest.mark.parametrize("bad", ["1.0", "v2", "2024-05-01", "1.0.0-rc1", ""])
def test_it_refuses_to_invent_a_successor(bad):
    """`1.0` could reasonably become `1.1` or `1.0.1`, and picking one writes a guess into
    somebody's published artifact — where the failure shows up much later, as an install that
    silently never supersedes."""
    with pytest.raises(VersionError):
        next_version(bad)


def test_the_refusal_names_the_value_and_the_way_out():
    with pytest.raises(VersionError) as e:
        next_version("v2")
    assert "'v2'" in str(e.value)
    assert "explicit version" in str(e.value)


# --------------------------------------------------------------------------- resolving


def test_omitting_it_bumps_the_patch():
    """The common case is a small change republished, and making the common case require an
    argument is exactly how it gets forgotten."""
    assert resolve_version("1.0.0", "") == "1.0.1"


def test_an_exact_number_is_taken_as_given():
    assert resolve_version("1.0.0", "2.0.0") == "2.0.0"


def test_keep_leaves_it_alone():
    """For re-running a publish that failed AFTER the number was raised. Bumping again would
    leave a gap and hide that the first attempt got that far."""
    assert resolve_version("1.4.0", "keep") == "1.4.0"


def test_a_lower_explicit_number_is_refused_here_not_by_the_service():
    """Caught locally, where we can still say what the published number is — a 409 arrives after
    a full pack and upload and says considerably less."""
    with pytest.raises(VersionError) as e:
        resolve_version("2.0.0", "1.9.9")
    assert "not higher" in str(e.value)


def test_ten_sorts_above_nine():
    """String comparison puts '1.10.0' below '1.9.0', which would refuse a legitimate publish."""
    assert resolve_version("1.9.0", "1.10.0") == "1.10.0"


# --------------------------------------------------------------------------- rewriting


AGENT_TOML = '''# Why this agent exists, and every decision behind it.
name = "Weather"
version = "1.0.0"   # bumped on every shipped change
description = "does weather"

[app]
title = "Weather"
version = "not this one"
'''


def test_the_rewrite_changes_only_the_version():
    out = rewrite_version(AGENT_TOML, "1.0.1")

    assert 'version = "1.0.1"' in out
    assert 'version = "not this one"' in out, "a [table] key called version must not be touched"


def test_every_comment_survives():
    """THE REASON THIS IS NOT A TOML ROUND-TRIP. Every writer in the standard library drops
    comments, and these files are mostly comments — a publish that quietly deleted an agent's
    documentation would be far worse than the bug being fixed."""
    out = rewrite_version(AGENT_TOML, "2.0.0")

    assert "# Why this agent exists" in out
    assert "# bumped on every shipped change" in out
    assert out.count("\n") == AGENT_TOML.count("\n"), "no lines added or lost"


def test_a_file_with_no_top_level_version_is_refused():
    with pytest.raises(VersionError) as e:
        rewrite_version('name = "x"\n\n[app]\nversion = "1.0.0"\n', "1.0.1")
    assert "agent.toml" in str(e.value)


def test_single_quotes_are_handled():
    assert "1.0.1" in rewrite_version("version = '1.0.0'\n", "1.0.1")

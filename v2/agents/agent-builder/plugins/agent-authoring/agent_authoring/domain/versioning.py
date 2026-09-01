"""What the next version is — and, deliberately, when there is no answer.

INSTALLS SUPERSEDE BY VERSION. Publishing the same number again reaches nobody: the service
refuses it with a 409 and, worse, a recipient who already has that number would never be offered
the new build. So every publish needs a higher number than the last, and asking a human to
remember that is asking them to fail at it — which is exactly what happened, repeatedly.

IT REFUSES RATHER THAN GUESSES. `1.0`, `v2`, `2024-05-01` and `""` are all things people
legitimately put in a version field, and none of them has an obvious successor: is `1.0` next
`1.1` or `1.0.1`? Inventing one writes a number into somebody's published artifact on a guess,
and the failure surfaces later as an install that silently never supersedes. A refusal with the
current value in it takes one sentence to resolve.
"""

from __future__ import annotations

import re

#: Strict three-part semver. Pre-release and build metadata (`1.0.0-rc1`, `1.0.0+build`) are
#: deliberately NOT matched: bumping them correctly means knowing whether the release or the
#: pre-release is the thing being superseded, and that is the author's call, not a default.
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

PARTS = ("major", "minor", "patch")


class VersionError(ValueError):
    """The message is for the author, verbatim."""


def next_version(current: str, part: str = "patch") -> str:
    """`1.2.3` + 'minor' -> `1.3.0`. Lower parts reset, which is the whole point of the scheme:
    a new minor starts at patch zero rather than carrying the old patch count forward."""
    current = (current or "").strip()
    if not current:
        raise VersionError(
            "this agent has no `version` in agent.toml, so there is nothing to bump. Add "
            'version = "1.0.0" and publish again.'
        )
    match = SEMVER.match(current)
    if not match:
        raise VersionError(
            f"cannot bump version {current!r} automatically — it is not MAJOR.MINOR.PATCH. "
            f"Pass an explicit version (e.g. version='1.0.1'), or set a semver number in "
            f"agent.toml first."
        )
    if part not in PARTS:
        raise VersionError(f"unknown bump {part!r} — use one of {', '.join(PARTS)}")

    major, minor, patch = (int(g) for g in match.groups())
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def resolve_version(current: str, requested: str) -> str:
    """What this publish should ship as.

    `requested` is what the caller asked for: a bump name, an explicit number, 'keep', or
    nothing at all (the default, which bumps the patch — the common case is a small change
    republished, and making the common case require an argument is how it gets forgotten).
    """
    requested = (requested or "").strip()
    if requested == "keep":
        # For re-running a publish that failed AFTER the number was already raised. Bumping
        # again would leave a gap and, worse, hide that the first attempt got that far.
        return current.strip()
    if not requested:
        return next_version(current, "patch")
    if requested in PARTS:
        return next_version(current, requested)
    if not SEMVER.match(requested):
        raise VersionError(
            f"version {requested!r} is not MAJOR.MINOR.PATCH, and not one of "
            f"{', '.join(PARTS)} or 'keep'."
        )
    if not _is_higher(requested, current):
        # Caught HERE rather than by the service's 409, because here we can still say what the
        # published number is and what to do about it.
        raise VersionError(
            f"version {requested} is not higher than the current {current}. Installs supersede "
            f"by version, so this would reach nobody who already has {current}."
        )
    return requested


def _is_higher(candidate: str, current: str) -> bool:
    """Numeric comparison, not string: '1.10.0' is above '1.9.0' and sorts below it as text."""
    left, right = SEMVER.match(candidate), SEMVER.match(current.strip())
    if left is None or right is None:
        return True  # unparseable current: the author named a number, take them at their word
    return tuple(int(g) for g in left.groups()) > tuple(int(g) for g in right.groups())


#: The top-level `version = "..."` line. Anchored to the start of a line and only matched BEFORE
#: the first [table] header, so a `version` inside [app] or a plugin table is never touched.
_VERSION_LINE = re.compile(r'^(\s*version\s*=\s*)(["\'])(.*?)\2', re.MULTILINE)


def rewrite_version(toml_text: str, new_version: str) -> str:
    """Replace the version VALUE and change nothing else.

    A TARGETED EDIT, not a parse-and-reserialise. These agent.toml files are mostly comments —
    the reasoning for every declaration lives in them — and every TOML writer in the standard
    library drops comments on the floor. A publish that silently deleted an agent's
    documentation would be a far worse bug than the one this function exists to fix.
    """
    head = toml_text.split("\n[", 1)[0]  # everything before the first table header
    match = _VERSION_LINE.search(head)
    if match is None:
        raise VersionError(
            "could not find a top-level `version = \"...\"` line in agent.toml to update. "
            "Set it by hand and publish again."
        )
    start, end = match.span(3)
    return toml_text[:start] + new_version + toml_text[end:]

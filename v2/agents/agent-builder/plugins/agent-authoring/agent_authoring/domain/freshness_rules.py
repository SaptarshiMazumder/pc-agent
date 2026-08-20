"""FreshnessRules — is the window that SHIPS built from the source that EXISTS?

A React agent keeps source in ``app/`` and built output in ``ui/``. The daemon serves ``ui/``, the
packer takes ``ui/``, and the marketplace ships ``ui/`` — nothing anywhere reads ``app/src``. So an
agent whose source has moved on from its build is an agent that looks finished, validates clean,
and delivers last week's screen to everybody who installs it.

THIS IS NOW THE EASY MISTAKE, which is why it is a rule rather than a note. Editing source and
building it used to be one action in a terminal; with ``build_app`` they are two, and the second
one is the forgettable one. The check exists because the failure is invisible from every angle an
author can see: the source is right, the tests pass, the files are all there, and the screen is
wrong.

PURE, like every rule here: it is GIVEN modification times and decides. The stat calls belong to
the reader, which is what keeps this testable without a filesystem and keeps every rule in this
package readable as a single sentence about what is wrong.

DELIBERATELY QUIET unless it is certain. No ``app/`` means nothing to build. No ``ui/`` at all is
somebody mid-scaffold, and layout rules already say so more usefully. Only a build that DEMONSTRABLY
predates its source is reported, because a freshness check that cries wolf is one an author learns
to ignore — and this one has to be believed the day it matters.
"""

from __future__ import annotations

from .finding import ERROR, Finding

#: Filesystems, archives and copies do not agree on sub-second times, and a checkout can land
#: source and output within the same tick. A build is called stale only when it is behind by more
#: than this — enough to clear that noise, far below any real edit-then-forget.
TOLERANCE_S = 2.0

#: Source that, changed, changes the built output. Config counts: an edited `vite.config.ts` moves
#: where the build writes, and `package.json` moves what it builds with.
_SOURCE_PREFIXES = ("app/src/",)
_SOURCE_FILES = ("app/vite.config.ts", "app/tsconfig.json", "app/package.json", "app/index.html")

#: Build output. `ui/index.html` is written by every vite run, so it is the honest witness for
#: "when was this last built" — a stray hand-edited file under ui/ is not.
_OUTPUT_FILES = ("ui/index.html",)


class FreshnessRules:
    """Compares when the source was last touched with when the window was last built."""

    name = "freshness"

    def check(self, spec, raw_toml: dict, files: list[str], mtimes: dict) -> list[Finding]:
        """:param mtimes: repo-relative path -> epoch seconds, from the reader. Paths it has no
        entry for are simply not considered; an empty mapping disables the check, which is what a
        caller that cannot stat should get rather than a false alarm."""
        if not mtimes:
            return []
        # A hand-written window has no build step, so there is nothing to be stale.
        if "app/package.json" not in files:
            return []

        newest_source, source_path = _newest(mtimes, self._sources(files))
        newest_output, _ = _newest(mtimes, [f for f in files if f in _OUTPUT_FILES])
        if newest_source is None or newest_output is None:
            # No source, or never built at all. Neither is this rule's business: an app with no
            # ui/ is caught by the layout rules, which say something more useful about it.
            return []

        behind = newest_source - newest_output
        if behind <= TOLERANCE_S:
            return []

        return [
            Finding(
                ERROR,
                "APP_BUILD_STALE",
                f"this agent's window was built BEFORE its source was last edited "
                f"({source_path} is {_ago(behind)} newer than the build). The daemon serves ui/ "
                f"and the packer ships ui/, so what anyone else sees is the older screen — the "
                f"change is real, on disk, and invisible.",
                path="ui/index.html",
                fix="call build_app on this agent, then validate again.",
            )
        ]

    @staticmethod
    def _sources(files: list[str]) -> list[str]:
        return [
            f
            for f in files
            if f in _SOURCE_FILES or any(f.startswith(p) for p in _SOURCE_PREFIXES)
        ]


def _newest(mtimes: dict, candidates: list[str]) -> tuple[float | None, str]:
    newest: float | None = None
    where = ""
    for path in candidates:
        stamp = mtimes.get(path)
        if stamp is None:
            continue
        if newest is None or stamp > newest:
            newest, where = stamp, path
    return newest, where


def _ago(seconds: float) -> str:
    """A gap, in the largest unit that keeps it readable. The number is the point — "3 days newer"
    is a forgotten build, "4 seconds newer" is somebody who just saved a file."""
    if seconds < 90:
        return f"{int(seconds)}s"
    if seconds < 5400:
        return f"{int(seconds // 60)}m"
    if seconds < 172800:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)} days"

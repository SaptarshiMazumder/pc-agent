"""BuildBackend — WHERE a window build runs, behind one port.

Two places can compile an agent's ``app/`` into its ``ui/``: the box the daemon is on (desktop —
node is bundled, the build is local and free), and the builder service (hosted — a node build's
memory peak is bigger than the whole daemon task, which is how a create used to OOM-kill the
daemon and every user's socket with it). ``BuildAppService`` must not know which is which: it
owns the ORDER of a build (resolve the app, build, verify something was written) while this port
owns the WHERE. The composition root picks the adapter off configuration, so the service, the
tools and the auto-build observer are byte-identical on both deployments.

THE CONTRACT: ``build(app_dir)`` leaves a fresh ``ui/`` beside ``app_dir`` and returns how it
went, or raises ``BuildBackendError`` carrying text fit to show VERBATIM — vite names files and
lines, and a backend that summarises turns a one-line fix into a hunt.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class BuildBackendError(Exception):
    """The build could not run or did not succeed. str(err) is shown to the author unchanged."""


@dataclass
class BuildBackendOutcome:
    #: How dependencies were provided ('linked' | 'present' | 'installed' | 'remote').
    dependencies: str
    #: The build log (vite's output; for remote builds, the builder's log tail).
    output: str


class BuildBackend(Protocol):
    def build(self, app_dir: Path) -> BuildBackendOutcome:
        """Compile app_dir into the sibling ui/ directory. Raises BuildBackendError on failure."""
        ...

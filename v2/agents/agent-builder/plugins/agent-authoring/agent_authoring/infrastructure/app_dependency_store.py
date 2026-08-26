"""AppDependencyStore — giving an agent's app its dependencies without downloading them again.

EVERY AGENT APP DECLARES THE SAME PACKAGES, because every one of them is a copy of the same
skeleton (templates/_skeleton). Installing them per agent would mean tens of megabytes from the
network every time a user creates one, a wait each time, that much disk per agent, and nothing at
all on a machine with no internet — none of which buys anything, since the dependency list is ours
rather than the agent author's.

So the product ships ONE installed tree (clients/desktop/scripts/build-runtime.ps1 builds it, the
supervisor passes its path as ``AGENTD_APP_DEPS``) and each agent's ``app/node_modules`` becomes a
LINK to it. Node resolves through a link exactly as through a directory, so vite, TypeScript and
every plugin behave as if the packages were installed locally.

WHY A JUNCTION ON WINDOWS rather than a symlink: creating a directory symlink there needs either
administrator rights or Developer Mode, and a build tool that works only for developers who have
turned on Developer Mode is a build tool that does not work. A junction needs neither.

FALLING BACK TO A REAL INSTALL is deliberate and not a silent downgrade — it is what a source
checkout does, where there is no product to have shipped a store. It is reported, because "this
took ninety seconds and needed the network" is something the caller should be able to say.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .node_toolchain import CommandOutput, NodeToolchain


@dataclass(frozen=True)
class DependencyOutcome:
    ok: bool
    #: 'linked' | 'present' | 'installed' | 'failed' — what actually happened, for the report.
    how: str
    detail: str = ""


class AppDependencyStore:
    """Makes ``<app>/node_modules`` resolve, by the cheapest honest means available."""

    def __init__(self, toolchain: NodeToolchain, env: dict | None = None):
        self._toolchain = toolchain
        self._env = dict(env if env is not None else os.environ)

    def shared_path(self) -> Path | None:
        """The product's shared tree, or None in a checkout that has none."""
        raw = (self._env.get("AGENTD_APP_DEPS") or "").strip()
        if not raw:
            return None
        path = Path(raw)
        return path if path.is_dir() else None

    def ensure(self, app_dir: Path) -> DependencyOutcome:
        modules = app_dir / "node_modules"
        # ALREADY RESOLVABLE. A link counts, and so does a real install a developer made by hand —
        # replacing either would throw away something that works.
        if modules.exists():
            return DependencyOutcome(True, "present")

        shared = self.shared_path()
        if shared is not None:
            linked = self._link(modules, shared)
            if linked.ok:
                return linked
            # A failed link is NOT fatal: installing is slower and needs the network, but it
            # produces a working app. Which one happened is reported either way.
            fallback = self._install(app_dir)
            return DependencyOutcome(
                fallback.ok,
                fallback.how,
                f"could not link to the shared store ({linked.detail}); installed instead",
            )

        return self._install(app_dir)

    # ------------------------------------------------------------------ linking

    def _link(self, modules: Path, shared: Path) -> DependencyOutcome:
        try:
            if os.name == "nt":
                # mklink is a cmd builtin, not an executable, so it cannot be invoked directly.
                done = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(modules), str(shared)],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if done.returncode != 0:
                    return DependencyOutcome(
                        False, "failed", ((done.stdout or "") + (done.stderr or "")).strip()
                    )
            else:
                os.symlink(shared, modules, target_is_directory=True)
        except (OSError, subprocess.SubprocessError) as e:
            return DependencyOutcome(False, "failed", str(e))
        if not (modules / "vite").exists():
            return DependencyOutcome(False, "failed", "the link resolves to a tree with no vite")
        return DependencyOutcome(True, "linked", str(shared))

    # ---------------------------------------------------------------- installing

    def _install(self, app_dir: Path) -> DependencyOutcome:
        result: CommandOutput = self._toolchain.npm(
            ["install", "--no-audit", "--no-fund", "--loglevel=error"], cwd=app_dir, timeout=900
        )
        if result.timed_out:
            return DependencyOutcome(
                False, "failed", "npm install ran for 15 minutes and was stopped"
            )
        if not result.ok:
            return DependencyOutcome(False, "failed", result.output)
        return DependencyOutcome(True, "installed")

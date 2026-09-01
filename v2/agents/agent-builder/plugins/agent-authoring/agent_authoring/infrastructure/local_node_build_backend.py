"""LocalNodeBuildBackend — the build runs HERE, with the node this box already has.

The desktop's backend (and the only choice on any machine without a builder service configured).
This is the pre-port behavior of BuildAppService moved verbatim behind the port: require a
toolchain, provide dependencies (shared store first), run ``npm run build``. The messages are
kept word-for-word — authors have read them for weeks and support answers link to them.
"""

from __future__ import annotations

from pathlib import Path

from agent_authoring.application.build_backend import BuildBackendError, BuildBackendOutcome
from agent_authoring.infrastructure.app_dependency_store import AppDependencyStore
from agent_authoring.infrastructure.node_toolchain import NodeMissing, NodeToolchain


class LocalNodeBuildBackend:
    def __init__(
        self,
        toolchain: NodeToolchain,
        dependencies: AppDependencyStore,
        build_timeout: float = 600.0,
    ):
        self._toolchain = toolchain
        self._dependencies = dependencies
        self._timeout = build_timeout

    def build(self, app_dir: Path) -> BuildBackendOutcome:
        try:
            self._toolchain.require()
        except NodeMissing as e:
            raise BuildBackendError(str(e)) from e

        deps = self._dependencies.ensure(app_dir)
        if not deps.ok:
            raise BuildBackendError(
                f"could not provide this app's dependencies, so it cannot be built.\n\n"
                f"{deps.detail or '(no detail)'}"
            )

        result = self._toolchain.npm(["run", "build"], cwd=app_dir, timeout=self._timeout)
        if result.timed_out:
            raise BuildBackendError(
                f"the build ran for {int(self._timeout)}s and was stopped. Partial output:\n\n"
                f"{result.output}"
            )
        if not result.ok:
            # VERBATIM. vite names the file and the line; replacing that with a verdict turns a
            # one-line fix into a hunt.
            raise BuildBackendError(f"the build failed:\n\n{result.output}")

        return BuildBackendOutcome(dependencies=deps.how, output=result.output)

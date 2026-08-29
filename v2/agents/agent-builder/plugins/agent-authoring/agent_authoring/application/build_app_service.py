"""BuildAppService — turn an agent's ``app/`` source into the ``ui/`` the daemon serves.

THE STEP THAT HAD NO TOOL. A React agent keeps source in ``app/`` and built output in ``ui/``, and
the daemon serves only ``ui/``. Editing ``app/src/App.tsx`` therefore changes NOTHING a user can
see until something runs vite — and until now the only way to run vite was a terminal. For anyone
who installed the product rather than cloning the repo, that meant a window they could edit and
could not rebuild: they change the source, reload, see the old screen, and nothing anywhere says
why.

WHAT IT DOES NOT DO: decide whether a build is needed. A caller that asks gets a build. Freshness
is validation's job (``ui/`` older than ``app/src`` blocks packing and publishing), and a tool that
sometimes silently does nothing is a tool nobody can reason about.

FAILURE IS REPORTED IN FULL, never summarised. A vite error names a file and a line; a service that
replaces that with "build failed" turns a fixable mistake into a search.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agent_authoring.application.build_backend import BuildBackend, BuildBackendError


class BuildAppError(Exception):
    """Carries the message the caller should show verbatim."""


@dataclass
class BuildAppResult:
    agent_id: str
    ok: bool
    #: 'linked' | 'present' | 'installed' — how the dependencies were provided this time.
    dependencies: str = ""
    #: What vite wrote. Kept whole on failure; trimmed to its tail on success, where the useful
    #: part is the file list and the rest is progress noise.
    output: str = ""
    written: list[str] = field(default_factory=list)

    @property
    def ui_dir(self) -> str:
        return "ui/"


class BuildAppService:
    """Builds one agent's app. Owns the ORDER of the steps and nothing else — WHERE the build
    runs is the injected BuildBackend's business (local node on desktop, the builder service on
    hosted), so this stays testable without a Node on the box and identical on both."""

    def __init__(self, reader, backend: BuildBackend):
        self._reader = reader
        self._backend = backend

    def build(self, agent_id: str) -> BuildAppResult:
        app_dir = self._app_dir(agent_id)

        try:
            outcome = self._backend.build(app_dir)
        except BuildBackendError as e:
            # VERBATIM — the backend's text already names files and lines.
            raise BuildAppError(str(e)) from e

        written = self._built_files(app_dir.parent / "ui")
        if not written:
            # The command succeeded and produced nothing, which means it is not the build we think
            # it is (a `build` script that lints, an outDir pointing elsewhere). Saying "built" here
            # would send the user to look for a change that was never written.
            raise BuildAppError(
                "the build reported success but wrote nothing to ui/. Check that app/vite.config.ts "
                "still has `outDir: '../ui'` — the daemon serves ui/ and nothing else.\n\n"
                f"{result.output}"
            )

        return BuildAppResult(
            agent_id=agent_id,
            ok=True,
            dependencies=outcome.dependencies,
            output=_tail(outcome.output),
            written=written,
        )

    # ------------------------------------------------------------------ helpers

    def _app_dir(self, agent_id: str) -> Path:
        agent_dir = self._reader.agent_dir(agent_id)
        if agent_dir is None:
            known = ", ".join(self._reader.known_ids()) or "(none)"
            raise BuildAppError(f"no agent '{agent_id}'. Known agents: {known}")
        app_dir = Path(agent_dir) / "app"
        if not (app_dir / "package.json").is_file():
            raise BuildAppError(
                f"'{agent_id}' has no app/ project to build — nothing here needs compiling. "
                f"An agent whose window is hand-written into ui/ is served straight off disk, so "
                f"a reload of its window is all it takes."
            )
        return app_dir

    @staticmethod
    def _built_files(ui_dir: Path) -> list[str]:
        if not ui_dir.is_dir():
            return []
        return sorted(
            p.relative_to(ui_dir).as_posix()
            for p in ui_dir.rglob("*")
            if p.is_file() and "node_modules" not in p.parts
        )


def _tail(output: str, lines: int = 12) -> str:
    """The end of a successful build's output — the file list. The head is progress noise, and a
    tool result is read by a model whose context is not free."""
    rows = [r for r in output.splitlines() if r.strip()]
    return "\n".join(rows[-lines:])

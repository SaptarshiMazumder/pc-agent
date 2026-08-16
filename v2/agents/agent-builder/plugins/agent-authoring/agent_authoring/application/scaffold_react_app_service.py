"""ScaffoldReactAppService — put a buildable React project in an agent's ``app/``.

WHAT IT DOES NOT DO IS THE POINT. It writes no components, no hooks, no layout — only the four
config files and the vendored SDK. What the window should BE is a judgement about this particular
agent, and the material for that judgement is ``agents/samples/``: working agents that already
solve the parts which are easy to get wrong and impossible to notice.

The vanilla ``scaffold_ui`` templates take the opposite bet, and it is the right bet for what they
are: a complete app copied onto disk, because a model writing one from a blank file reliably gets
the event wiring wrong in ways that look like nothing at all. That bet stops paying once the app
is a React project — there is no single right shape for one, the samples differ from each other on
purpose, and a copied app would be a fourth opinion competing with them.

WHY THE SDK IS COPIED RATHER THAN DEPENDED ON. In this repo the samples reach it by relative path
(``file:../../../../clients/sdk-js``). That path exists nowhere else. An agent scaffolded into the
user's own agents directory would fail at ``npm install`` — for every recipient, never for the
author. So the bundle and its types are copied in and aliased. The SDK's own build refreshes the
source copy, so it cannot drift from the daemon it talks to.

NEVER CLOBBERS, same rule as ScaffoldUiService: an existing ``app/`` is somebody's work.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from agent_runtime.application.write_scope import WriteRefused, check_write

#: What the starter is made of, relative to ``_borrowed/react/``. Named rather than globbed so a
#: missing piece is a loud failure at scaffold time instead of a build error later — and so the
#: list itself says what an agent app needs.
STARTER_FILES = (
    "package.json",
    "tsconfig.json",
    "vite.config.ts",
    "index.html",
    "README.md",
    "vendor/agentd-client.js",
    "vendor/agentd-client.d.ts",
)


class ReactScaffoldError(Exception):
    """Carries the message the caller should show verbatim."""


@dataclass
class ReactScaffoldResult:
    agent_id: str
    app_dir: Path
    written: list[str] = field(default_factory=list)

    @property
    def readme_path(self) -> str:
        return "app/README.md"


class ScaffoldReactAppService:
    """Copies ``_borrowed/react/`` into ``agents/<id>/app/``.

    :param reader: resolves an agent id to its directory.
    :param starter_root: ``templates/_borrowed/react/`` — injected so a test can point it at a
        tmp_path, and because knowing where this bundle sits is the composition root's business.
    """

    def __init__(self, reader, starter_root: Path):
        self._reader = reader
        self._starter_root = Path(starter_root)

    def scaffold(self, agent_id: str, confirm_overwrite: bool = False) -> ReactScaffoldResult:
        agent_dir = self._reader.agent_dir(agent_id)
        if agent_dir is None:
            known = ", ".join(self._reader.known_ids()) or "(none)"
            raise ReactScaffoldError(f"no agent '{agent_id}'. Known agents: {known}")

        # Resolve every source BEFORE writing. A half-copied project looks scaffolded and fails
        # at build time with a missing-module error that names nothing useful.
        plan: list[tuple[str, Path]] = []
        for rel in STARTER_FILES:
            src = self._starter_root / rel
            if not src.is_file():
                raise ReactScaffoldError(
                    f"the React starter is incomplete — missing {rel} at {src}. "
                    f"For the vendored SDK, run `npm run build` in clients/sdk-js."
                )
            plan.append((rel, src))

        app_dir = agent_dir / "app"
        existing = self._existing(app_dir)
        if existing and not confirm_overwrite:
            raise ReactScaffoldError(
                f"REFUSING to scaffold over the existing app/ in '{agent_id}'.\n"
                f"It already has: {', '.join(existing[:12])}"
                f"{' …' if len(existing) > 12 else ''}.\n"
                f"Ask the user. To change one file, edit it with `write` — that keeps the rest. "
                f"If they genuinely want this project replaced, call again with "
                f"confirm_overwrite=true."
            )

        try:
            check_write(app_dir)
        except WriteRefused as e:
            raise ReactScaffoldError(str(e)) from e

        written: list[str] = []
        for rel, src in plan:
            dest = app_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
            written.append(rel)

        return ReactScaffoldResult(agent_id=agent_id, app_dir=app_dir, written=sorted(written))

    @staticmethod
    def _existing(app_dir: Path) -> list[str]:
        """What is already in ``app/``, ignoring installed dependencies — node_modules is not
        somebody's work, it is 200MB of somebody else's."""
        if not app_dir.is_dir():
            return []
        return sorted(
            p.relative_to(app_dir).as_posix()
            for p in app_dir.rglob("*")
            if p.is_file() and "node_modules" not in p.parts
        )

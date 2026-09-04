"""ScaffoldReactAppService — put a WORKING window in an agent's ``app/``.

WHAT CHANGED, AND WHY IT IS THE WHOLE POINT. This used to write config files, the vendored SDK and
``src/main.tsx`` — and nothing else. What the window should BE was left as a judgement about the
agent, and the material for that judgement was ``agents/samples/``: finished agents to read and
learn from.

That bet did not pay. Reading a sample is optional, and the parts that get skipped are the parts
that are invisible when missing: the app is signed in but never shows a balance, or shows a
balance but has no way to join the organization that paid for it. Every one of those failures is
silent for the author and total for whoever installs the agent. The samples are gone; the skeleton
replaces them, and it is a stronger thing than they were — not an example to copy from, but the
actual artifact the agent starts as.

SO A SCAFFOLD IS A COPY OF A COMPLETE APP. Shell, conversation, and the four screens every agent
on this platform shares — sign-in, credits, settings, organizations — already wired and already
working. The model then EDITS a running window instead of assembling one, which is a different and
much smaller job, and one where a mistake shows up immediately rather than at install time.

``src/common/`` IS TAKEN FROM ITS OWN SOURCE, not from the skeleton's copy of it. The skeleton
carries a copy so it can be built and typechecked on its own, but `validate_agent` compares what
an agent ships against ``templates/_common/`` — so that is what gets written here, and the two can
never disagree about which one an agent received.

WHY THE SDK IS COPIED RATHER THAN DEPENDED ON. In this repo the workspace reaches it by relative
path (``file:../../../../clients/sdk-js``). That path exists nowhere else. An agent scaffolded into
the user's own agents directory would fail at ``npm install`` — for every recipient, never for the
author. So the bundle and its types are copied in and aliased. The SDK's own build refreshes the
source copy, so it cannot drift from the daemon it talks to.

NEVER CLOBBERS: an existing ``app/`` is somebody's work.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from agent_runtime.application.write_scope import WriteRefused, check_write

log = logging.getLogger("agentd")

#: Never copied into an agent. Build output and installed packages are not the template.
SKIP_DIRS = frozenset({"node_modules", "dist", "ui", "__pycache__", ".vite"})

#: Where the shared modules land inside an agent, and where they are read from.
COMMON_DEST = "src/common"
COMMON_ROOT_NAME = "_common"

#: Proof the skeleton is really there rather than an empty directory that would scaffold an agent
#: with no window at all. Checked before anything is written.
REQUIRED = (
    "package.json",
    "index.html",
    "src/main.tsx",
    "src/App.tsx",
    "vendor/agentd-client.js",
)


class ReactScaffoldError(RuntimeError):
    pass


@dataclass
class ReactScaffoldResult:
    agent_id: str
    app_dir: Path
    written: list[str] = field(default_factory=list)
    #: True when the PREBUILT template window was installed as ui/ — the agent is openable with
    #: no build having run anywhere. False falls back to compiling (previews absent/stale-proof).
    ui_installed: bool = False


class ScaffoldReactAppService:
    """:param skeleton_root: ``templates/_skeleton``. Injected so a test can point at a fixture
    rather than staging the whole templates tree."""

    def __init__(
        self,
        reader,
        skeleton_root: Path,
        common_root: Path | None = None,
        variants_root: Path | None = None,
    ):
        self._reader = reader
        self._skeleton_root = Path(skeleton_root)
        # Defaults to `_common/` beside `_skeleton/`, which is where it lives.
        self._common_root = (
            Path(common_root)
            if common_root is not None
            else self._skeleton_root.parent / COMMON_ROOT_NAME
        )
        # `_variants/` beside it: one folder per window shape. Missing entirely is fine — the
        # skeleton alone is the chat template, which is also why an unknown template FALLS BACK
        # to it rather than failing: the agent is mid-creation by the time this runs, and a
        # misspelled template name should cost a note, not the creation.
        self._variants_root = (
            Path(variants_root)
            if variants_root is not None
            else self._skeleton_root.parent / "_variants"
        )
        # `_prebuilt/` beside both: each template ALREADY BUILT, Gate and all
        # (build_prebuilt_templates.py, run by the SDK vendor pipeline). Scaffolding copies the
        # matching one in as ui/, so a new agent is openable without any build running here —
        # which on a hosted daemon is the difference between working and OOM-killing the task.
        # NOT `_previews/`: those are the create dialog's DISPLAY builds, deliberately Gate-less
        # so a thumbnail shows the layout — shipped into an agent they would bypass sign-in.
        self._prebuilt_root = self._skeleton_root.parent / "_prebuilt"

    def templates(self) -> list[str]:
        """The template names on offer — the folders in `_variants/`. The skeleton needs no
        entry: `chat` is an empty overlay folder, present so this list is the whole answer."""
        if not self._variants_root.is_dir():
            return ["chat"]
        return sorted(d.name for d in self._variants_root.iterdir() if d.is_dir())

    def scaffold(
        self, agent_id: str, confirm_overwrite: bool = False, template: str = "chat"
    ) -> ReactScaffoldResult:
        agent_dir = self._reader.agent_dir(agent_id)
        if agent_dir is None:
            known = ", ".join(self._reader.known_ids()) or "(none)"
            raise ReactScaffoldError(f"no agent '{agent_id}'. Known agents: {known}")

        # RESOLVE EVERYTHING BEFORE WRITING ANYTHING. A half-copied project looks scaffolded and
        # fails at build time with a missing-module error that names nothing useful.
        plan = self._plan(template)

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

        ui_installed = self._install_prebuilt_ui(template, agent_dir / "ui")

        return ReactScaffoldResult(
            agent_id=agent_id,
            app_dir=app_dir,
            written=sorted(written),
            ui_installed=ui_installed,
        )

    def _install_prebuilt_ui(self, template: str, ui_dir: Path) -> bool:
        """Copy the template's PREBUILT window in as ui/ — the source just scaffolded is exactly
        what the preview was built from, so this is the build's output without the build.

        False (never an error) when there is nothing suitable: previews are an optimization, and
        the caller's fallback is the real compile. An EXISTING ui/ is left alone for the same
        reason an existing app/ refuses the scaffold: it is somebody's work."""
        wanted = (template or "chat").strip().lower() or "chat"
        preview = self._prebuilt_root / wanted
        if not (preview / "index.html").is_file():
            preview = self._prebuilt_root / "chat"
        if not (preview / "index.html").is_file():
            return False
        if ui_dir.exists() and any(ui_dir.iterdir()):
            return False
        # `copy_function=copyfile`, NOT the default copy2, and it is load-bearing rather than
        # tidiness: copy2 preserves the TEMPLATE's mtime, so a window copied in here carried the
        # date the template was last built -- days or weeks old -- while the app/ sources beside
        # it (written with copyfile, just above) carried "now".
        #
        # The freshness rule compares exactly those two things, so every freshly scaffolded agent
        # was born failing APP_BUILD_STALE: "your window was built BEFORE its source was last
        # edited", on an agent nobody had touched yet. The build was not stale -- this preview IS
        # the build of the source just written, which is the whole point of installing it -- only
        # the timestamp said otherwise, and it sent authors to fix a defect that did not exist.
        #
        # Copying without metadata stamps the window "now". Because ui/ is installed AFTER app/,
        # that is strictly newer than every source file, which is the truth the rule is asking
        # about.
        shutil.copytree(preview, ui_dir, dirs_exist_ok=True, copy_function=shutil.copyfile)
        return True

    # ------------------------------------------------------------------ planning
    def _plan(self, template: str = "chat") -> list[tuple[str, Path]]:
        """``[(path inside app/, file to copy)]`` — the whole skeleton, then the TEMPLATE's
        overlay (variant wins per file), then the shared modules over the top.

        THE ORDER IS THE ARCHITECTURE. The base is written once; a variant folder holds only what
        differs and its files import the base's — which resolve because both land in one tree.
        `_common/` goes last so no template can ship a drifted copy of a shared screen."""
        if not self._skeleton_root.is_dir():
            raise ReactScaffoldError(
                f"the skeleton is missing at {self._skeleton_root}. An agent cannot be given a "
                f"window without it."
            )

        plan: dict[str, Path] = {}
        for src in sorted(self._skeleton_root.rglob("*")):
            if not src.is_file():
                continue
            rel = src.relative_to(self._skeleton_root)
            if any(part in SKIP_DIRS for part in rel.parts):
                continue
            plan[rel.as_posix()] = src

        # THE TEMPLATE OVERLAY. `chat` is an empty folder, so the base passes through untouched.
        wanted = (template or "chat").strip().lower() or "chat"
        variant_dir = self._variants_root / wanted
        if not variant_dir.is_dir() and wanted != "chat":
            log.warning(
                "unknown template '%s' — using 'chat'. On offer: %s",
                wanted,
                ", ".join(self.templates()),
            )
            variant_dir = self._variants_root / "chat"
        if variant_dir.is_dir():
            for src in sorted(variant_dir.rglob("*")):
                if not src.is_file():
                    continue
                rel = src.relative_to(variant_dir)
                if any(part in SKIP_DIRS for part in rel.parts) or rel.name == "README.md":
                    continue
                plan[rel.as_posix()] = src

        missing = [r for r in REQUIRED if r not in plan]
        if missing:
            raise ReactScaffoldError(
                f"the skeleton at {self._skeleton_root} is incomplete — missing "
                f"{', '.join(missing)}. For the vendored SDK, run `npm run build` in "
                f"clients/sdk-js."
            )

        # THE SHARED MODULES FROM THEIR OWN SOURCE. Globbed rather than named: the set grows, and
        # a list to keep in step is a list that falls out of step. `validate_agent` compares what
        # landed against this same directory, so an omission is caught there rather than by a
        # build error naming nothing useful.
        common = sorted(f for f in self._common_root.rglob("*") if f.is_file())
        if not common:
            raise ReactScaffoldError(
                f"the common modules are missing at {self._common_root} — an agent scaffolded "
                f"without them has no sign-in, no credits page and no organizations, and cannot "
                f"be published."
            )
        for src in common:
            plan[f"{COMMON_DEST}/{src.relative_to(self._common_root).as_posix()}"] = src

        return sorted(plan.items())

    @staticmethod
    def _existing(app_dir: Path) -> list[str]:
        if not app_dir.is_dir():
            return []
        return sorted(
            p.name for p in app_dir.iterdir() if p.name not in SKIP_DIRS and not p.name.startswith(".")
        )

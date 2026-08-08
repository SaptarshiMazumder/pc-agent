"""ScaffoldUiService — materialise a working ``ui/`` for an agent.

The reason this is a tool and not a paragraph in a skill: every app the builder has written
from a blank file got the SAME two protocol details wrong, and both are invisible at runtime.
Prose describing the right shape is read once and competes with the model's priors. A file
copied onto disk does not compete with anything — it is simply already correct, and what the
model does next is edit working code rather than recall a spec.

Ranked by strength, this bundle now has all three: a validator rule that fails
(``UiRules``), an instruction present every turn (agent-builder's ``AGENTS.md``), and — here —
a starting artifact that was never wrong in the first place.

NEVER CLOBBERS. An existing ``ui/`` is somebody's work. This refuses and names every file it
would have replaced, and the caller has to come back with ``confirm_overwrite`` after asking
the user. That rule was learned the expensive way: a tool whose refusal message offered a
one-step workaround got the workaround taken, and a real agent lost its app.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from agent_authoring.domain.ui_template import UiTemplate, UiTemplates


class ScaffoldError(Exception):
    """The scaffold could not proceed. Carries the message the caller should show verbatim."""


@dataclass
class ScaffoldResult:
    agent_id: str
    template: UiTemplate
    ui_dir: Path
    written: list[str] = field(default_factory=list)
    replaced: list[str] = field(default_factory=list)

    @property
    def readme_path(self) -> str:
        return f"ui/{self.template.readme}"


class ScaffoldUiService:
    """Copies a template into ``agents/<id>/ui/``.

    :param reader: resolves an agent id to its directory (the bundle's only FS reader).
    :param templates: the catalogue.
    :param template_root: where the templates live — ``skills/build-agent/templates/``.
    :param borrow_root: the LIVE ``ui/`` that ``borrowed`` files are taken from.

    Both roots are injected rather than derived here: knowing where this bundle sits inside
    the product is the composition root's business, and injecting it is what lets a test point
    the whole thing at a tmp_path.
    """

    def __init__(self, reader, templates: UiTemplates, template_root: Path, borrow_root: Path):
        self._reader = reader
        self._templates = templates
        self._template_root = Path(template_root)
        self._borrow_root = Path(borrow_root)

    def scaffold(
        self, agent_id: str, template_id: str = "", confirm_overwrite: bool = False
    ) -> ScaffoldResult:
        template = self._templates.get(template_id)
        if template is None:
            raise ScaffoldError(
                f"no ui template '{template_id}'. Available:\n{self._templates.describe()}"
            )

        agent_dir = self._reader.agent_dir(agent_id)
        if agent_dir is None:
            known = ", ".join(self._reader.known_ids()) or "(none)"
            raise ScaffoldError(f"no agent '{agent_id}'. Known agents: {known}")

        # Resolve every source BEFORE writing anything. A half-copied ui/ is worse than a
        # refusal: it looks scaffolded, and the missing file only surfaces as a 404 in the
        # window. A template file that is not on disk is a broken install, not a soft case.
        plan: list[tuple[str, Path]] = []
        for rel in template.files:
            src = self._template_root / template.id / rel
            if not src.is_file():
                raise ScaffoldError(
                    f"template '{template.id}' is incomplete — missing {rel} at {src}"
                )
            plan.append((rel, src))
        for rel in template.borrowed:
            src = self._borrow_root / rel
            if not src.is_file():
                raise ScaffoldError(
                    f"cannot scaffold: {rel} is missing from {self._borrow_root} — it is copied "
                    f"from the live UI so there is only ever one copy in the product"
                )
            plan.append((rel, src))

        ui_dir = agent_dir / "ui"
        replaced = sorted(
            rel for rel, _ in plan if (ui_dir / rel).is_file()
        )
        existing = self._existing(ui_dir)
        if existing and not confirm_overwrite:
            raise ScaffoldError(
                f"REFUSING to scaffold over the existing ui/ in '{agent_id}'.\n"
                f"It already has: {', '.join(existing)}.\n"
                f"This would overwrite: {', '.join(replaced) if replaced else 'nothing'}.\n"
                f"Ask the user. If they only want a change, edit the specific file with "
                f"`write` — it keeps everything else. If they genuinely want this app "
                f"replaced, call again with confirm_overwrite=true."
            )

        written: list[str] = []
        for rel, src in plan:
            dest = ui_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
            written.append(rel)

        return ScaffoldResult(
            agent_id=agent_id,
            template=template,
            ui_dir=ui_dir,
            written=sorted(written),
            replaced=replaced,
        )

    @staticmethod
    def _existing(ui_dir: Path) -> list[str]:
        """What is already in ``ui/``, as relative POSIX paths. Empty when there is no app."""
        if not ui_dir.is_dir():
            return []
        return sorted(
            p.relative_to(ui_dir).as_posix()
            for p in ui_dir.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts
        )

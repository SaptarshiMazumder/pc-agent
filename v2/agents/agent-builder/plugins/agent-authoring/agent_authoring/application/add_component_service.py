"""AddComponentService — weave a UiComponent into an app that already exists.

PLAN, THEN APPLY. ``plan()`` decides everything and writes nothing, so a caller can show what would
change (and a test can assert it) without touching an agent. Every step carries its own state, and
the three that matter are:

    already-present   the component's ``detect`` matched. Nothing is done.
    write / patch     a deterministic change with a known target.
    manual            the anchor is missing, so the code is HANDED BACK instead of guessed at.

IDEMPOTENT BY CONSTRUCTION. Re-applying is a no-op, which is what makes it safe for a model to
apply a component whenever it is unsure, and what lets the catalogue be run across every agent in a
repo without auditing each one first.

NEVER GUESSES AT UNKNOWN CODE. A hand-written app.js has no anchor comment. The service still does
every deterministic step — copies files, refreshes the SDK, adds the <script> tag, appends the theme
tokens — and then states the snippet and where it belongs. A regex that inserted into unrecognised
code would sometimes land inside a string, a comment, or the wrong function, and the result would be
a file that looks patched and does not run.

NEVER CLOBBERS. A component file that exists with different content is reported and skipped unless
``confirm_overwrite``. Same rule as scaffolding, learned the same expensive way.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from agent_authoring.domain.ui_component import UiComponent, UiComponents

# States a step can be in. Strings rather than an enum so they cross the tool boundary into
# ToolResult.details unchanged and stay readable in a transcript.
DONE = "done"
PRESENT = "already-present"
MANUAL = "manual"
BLOCKED = "blocked"


class ComponentError(Exception):
    """Cannot proceed. The message is what the caller should show, verbatim."""


@dataclass
class Step:
    kind: str  # "file" | "script" | "style" | "insert"
    target: str  # relative to ui/
    state: str
    detail: str = ""
    payload: str = ""  # for MANUAL steps: the exact code to place

    @property
    def changed(self) -> bool:
        return self.state == DONE


@dataclass
class ComponentPlan:
    agent_id: str
    component: UiComponent
    ui_dir: Path
    steps: list[Step] = field(default_factory=list)

    @property
    def manual(self) -> list[Step]:
        return [s for s in self.steps if s.state == MANUAL]

    @property
    def blocked(self) -> list[Step]:
        return [s for s in self.steps if s.state == BLOCKED]

    @property
    def changes(self) -> list[Step]:
        return [s for s in self.steps if s.changed]

    @property
    def nothing_to_do(self) -> bool:
        return not self.changes and not self.manual and not self.blocked


class AddComponentService:
    """:param reader: resolves an agent id to its directory.
    :param components: the catalogue.
    :param component_root: templates/components/ — where a component's OWN files live.
    :param borrow_root: Agent Builder's LIVE ui/, the single source for borrowed files."""

    def __init__(self, reader, components: UiComponents, component_root: Path, borrow_root: Path):
        self._reader = reader
        self._components = components
        self._component_root = Path(component_root)
        self._borrow_root = Path(borrow_root)

    # ------------------------------------------------------------------ plan
    def plan(
        self, agent_id: str, component_id: str, confirm_overwrite: bool = False
    ) -> ComponentPlan:
        component = self._components.get(component_id)
        if component is None:
            raise ComponentError(
                f"no ui component '{component_id}'. Available:\n{self._components.describe()}"
            )
        agent_dir = self._reader.agent_dir(agent_id)
        if agent_dir is None:
            known = ", ".join(self._reader.known_ids()) or "(none)"
            raise ComponentError(f"no agent '{agent_id}'. Known agents: {known}")
        ui_dir = agent_dir / "ui"
        if not ui_dir.is_dir():
            raise ComponentError(
                f"'{agent_id}' has no ui/ yet, so there is nothing to add a component TO. "
                "Run scaffold_ui first — it can include components in one step."
            )

        plan = ComponentPlan(agent_id=agent_id, component=component, ui_dir=ui_dir)
        plan.steps += self._file_steps(component, ui_dir, confirm_overwrite)
        plan.steps += self._script_steps(component, ui_dir)
        plan.steps += self._style_steps(component, ui_dir)
        plan.steps += self._insert_steps(component, ui_dir)
        return plan

    # ------------------------------------------------------------------ apply
    def apply(self, plan: ComponentPlan) -> ComponentPlan:
        """Perform every DONE step. Ordered so a file exists before anything references it."""
        if plan.blocked:
            raise ComponentError(
                "refusing to apply: "
                + "; ".join(f"{s.target} — {s.detail}" for s in plan.blocked)
            )
        for step in plan.steps:
            if not step.changed:
                continue
            if step.kind == "file":
                self._copy(step, plan)
            elif step.kind == "script":
                self._add_script(step, plan)
            elif step.kind == "style":
                self._append_style(step, plan)
            elif step.kind == "insert":
                self._insert(step, plan)
        return plan

    # ------------------------------------------------------------------ steps: files
    def _source_for(self, component: UiComponent, relative: str) -> Path:
        root = self._borrow_root if relative in component.borrowed else self._component_root / component.id
        return root / relative

    def _file_steps(
        self, component: UiComponent, ui_dir: Path, confirm_overwrite: bool
    ) -> list[Step]:
        steps = []
        for relative in component.all_files:
            source = self._source_for(component, relative)
            if not source.is_file():
                steps.append(
                    Step(
                        "file",
                        relative,
                        BLOCKED,
                        f"missing from this install ({source}) — a broken install, not a soft case",
                    )
                )
                continue
            target = ui_dir / relative
            if target.is_file():
                same = target.read_bytes() == source.read_bytes()
                if same:
                    steps.append(Step("file", relative, PRESENT, "identical"))
                    continue
                if not confirm_overwrite:
                    # A BORROWED file is the SDK: refreshing it is the whole point of borrowing, and
                    # an out-of-date copy is what makes a component fail with "not a function". A
                    # component's OWN file may have been edited by the author, so that one needs a
                    # decision.
                    if relative in component.borrowed:
                        steps.append(Step("file", relative, DONE, "refreshed from the live SDK"))
                        continue
                    steps.append(
                        Step(
                            "file",
                            relative,
                            BLOCKED,
                            "exists and differs — pass confirm_overwrite=true to replace it, after "
                            "asking the user",
                        )
                    )
                    continue
                steps.append(Step("file", relative, DONE, "REPLACED"))
                continue
            steps.append(Step("file", relative, DONE, "new"))
        return steps

    def _copy(self, step: Step, plan: ComponentPlan) -> None:
        source = self._source_for(plan.component, step.target)
        target = plan.ui_dir / step.target
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)

    # ------------------------------------------------------------------ steps: index.html
    ENTRY = "index.html"

    def _script_steps(self, component: UiComponent, ui_dir: Path) -> list[Step]:
        if not component.scripts:
            return []
        entry = ui_dir / self.ENTRY
        if not entry.is_file():
            return [
                Step("script", self.ENTRY, BLOCKED, f"no {self.ENTRY} in {ui_dir}")
            ]
        html = entry.read_text(encoding="utf-8")
        loaded = self._loaded_scripts(html)
        steps = []
        for src in component.scripts:
            if src in loaded:
                steps.append(Step("script", src, PRESENT, f"already in {self.ENTRY}"))
            else:
                steps.append(Step("script", src, DONE, f"added to {self.ENTRY}"))
        return steps

    @staticmethod
    def _loaded_scripts(html: str) -> set[str]:
        """Every script src already in the document, with any CACHE BUSTER stripped.

        An exact string match is not enough. figure-creator loads the SDK as
        `vendor/agentd-client.js?v=4`, so a literal comparison said "not present" and this added a
        SECOND <script> for the same file — the SDK loaded twice, which is at best wasted work and
        at worst a double initialisation. The query string is not part of which file is loaded.
        """
        import re

        found = set()
        for raw in re.findall(r"""<script[^>]*\bsrc\s*=\s*["']([^"']+)["']""", html, re.I):
            found.add(raw.split("?", 1)[0].split("#", 1)[0].strip())
        return found

    def _add_script(self, step: Step, plan: ComponentPlan) -> None:
        entry = plan.ui_dir / self.ENTRY
        html = entry.read_text(encoding="utf-8")
        tag = f'  <script src="{step.target}"></script>\n'
        # BEFORE the first existing <script>, because load order is the bug this prevents: the SDK
        # has to be defined before app.js runs. Falling back to </body> keeps a document with no
        # scripts working.
        marker = html.find("<script")
        if marker == -1:
            marker = html.rfind("</body>")
        if marker == -1:
            entry.write_text(html + tag, encoding="utf-8")
            return
        line_start = html.rfind("\n", 0, marker) + 1
        entry.write_text(html[:line_start] + tag + html[line_start:], encoding="utf-8")

    # ------------------------------------------------------------------ steps: style.css
    STYLESHEET = "style.css"

    def _style_steps(self, component: UiComponent, ui_dir: Path) -> list[Step]:
        if not component.styles.strip():
            return []
        sheet = ui_dir / self.STYLESHEET
        if sheet.is_file():
            existing = sheet.read_text(encoding="utf-8")
            marker = component.style_marker or component.styles.strip().splitlines()[0]
            if marker and marker in existing:
                return [Step("style", self.STYLESHEET, PRESENT, "tokens already defined")]
        return [Step("style", self.STYLESHEET, DONE, "theme tokens appended")]

    def _append_style(self, step: Step, plan: ComponentPlan) -> None:
        sheet = plan.ui_dir / self.STYLESHEET
        existing = sheet.read_text(encoding="utf-8") if sheet.is_file() else ""
        separator = "" if existing.endswith("\n") or not existing else "\n"
        sheet.write_text(existing + separator + plan.component.styles, encoding="utf-8")

    # ------------------------------------------------------------------ steps: code
    def _insert_steps(self, component: UiComponent, ui_dir: Path) -> list[Step]:
        steps = []
        for insertion in component.insert:
            target = ui_dir / insertion.file
            if not target.is_file():
                steps.append(
                    Step("insert", insertion.file, BLOCKED, f"no {insertion.file} in {ui_dir}")
                )
                continue
            code = target.read_text(encoding="utf-8")
            if insertion.present_in(code):
                steps.append(
                    Step("insert", insertion.file, PRESENT, "this app already signs the user in")
                )
                continue
            if insertion.anchor and insertion.anchor in code:
                steps.append(Step("insert", insertion.file, DONE, f"inserted at {insertion.anchor}"))
                continue
            steps.append(
                Step(
                    "insert",
                    insertion.file,
                    MANUAL,
                    f"no `{insertion.anchor}` anchor in {insertion.file} — "
                    + (insertion.note or "place the snippet yourself"),
                    payload=insertion.snippet,
                )
            )
        return steps

    def _insert(self, step: Step, plan: ComponentPlan) -> None:
        insertion = next(i for i in plan.component.insert if i.file == step.target)
        target = plan.ui_dir / step.target
        lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
        out = []
        placed = False
        for line in lines:
            out.append(line)
            if placed or insertion.anchor not in line:
                continue
            # Match the anchor's own indentation rather than the declared default, so the snippet
            # lands aligned with the code around it whatever the file's style is.
            indent = line[: len(line) - len(line.lstrip())] or insertion.indent
            out += [
                (indent + snippet_line if snippet_line.strip() else snippet_line) + "\n"
                for snippet_line in insertion.snippet.splitlines()
            ]
            placed = True
        target.write_text("".join(out), encoding="utf-8")

    # ------------------------------------------------------------------ SDK capability check
    def missing_sdk_symbols(self, plan: ComponentPlan) -> list[str]:
        """Symbols this component needs that the agent's VENDORED SDK does not have.

        Checked AFTER applying, because applying refreshes the SDK — reporting a stale-SDK problem
        that the same call just fixed is the kind of false alarm that gets a check ignored.
        """
        if not plan.component.requires:
            return []
        vendored = plan.ui_dir / "vendor" / "agentd-client.js"
        if not vendored.is_file():
            return list(plan.component.requires)
        text = vendored.read_text(encoding="utf-8", errors="ignore")
        return [symbol for symbol in plan.component.requires if symbol not in text]

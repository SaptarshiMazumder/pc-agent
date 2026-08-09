"""add_ui_component — add a reusable piece to an app that ALREADY EXISTS.

The gap this fills: ``scaffold_ui`` copies a whole app and refuses over an existing ``ui/``, so
there was no way to add one capability to an agent someone had already built. The only options were
re-scaffold (destroying their work) or hand-edit, and hand-editing is how an agent ends up with a
sign-in call and a year-old SDK that cannot run it.

SAFE TO CALL WHEN UNSURE — the operation is idempotent. Every step checks whether it is already
done, so running this on an app that has the component reports "already present" and changes
nothing. That property is what makes it usable by a model.

The description below is BUILT FROM THE CATALOGUE, so a component added to ``UiComponents`` is
offered here automatically instead of existing but being un-nameable.
"""

from __future__ import annotations

from agent_authoring.application.add_component_service import (
    BLOCKED,
    DONE,
    MANUAL,
    PRESENT,
    ComponentError,
)
from agent_runtime.application.interfaces.tool import Tool, ToolResult


class AddUiComponentTool(Tool):
    name = "add_ui_component"
    label = "Add UI Component"
    default_retryable = False  # writes files

    def __init__(self, service, components):
        self._service = service
        self._components = components
        self.description = (
            "ADD A REUSABLE PIECE to an agent's EXISTING app — the way to add a capability without "
            "rewriting or re-scaffolding someone's ui/. It copies any files the component needs, "
            "refreshes the vendored SDK, adds the <script> tags, appends its theme tokens, and "
            "weaves in its code.\n"
            "SAFE TO RE-RUN: every step checks first, so a component that is already there is "
            "reported and nothing changes. Use it whenever you are unsure whether an app has "
            "something.\n"
            "If the app's code has no anchor comment (a hand-written app.js), it still does every "
            "file-level step and then TELLS you the exact snippet and where to put it — it will not "
            "guess its way into code it does not recognise.\n"
            "Components:\n" + self._components.describe() + "\n"
            "For a NEW agent, pass components to scaffold_ui instead — same result in one call."
        )
        self.parameters = {
            "type": "object",
            "required": ["agent_id", "component"],
            "properties": {
                "agent_id": {"type": "string", "description": "the agent whose app to add to"},
                "component": {
                    "type": "string",
                    "enum": list(self._components.ids()),
                    "description": "which component to add",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "true = report what would change and write nothing",
                },
                "confirm_overwrite": {
                    "type": "boolean",
                    "description": "REQUIRED only when one of the component's own files already "
                    "exists with different content — the author may have edited it. Ask the user "
                    "first; never set it on your own initiative",
                },
            },
        }

    # ------------------------------------------------------------------ rendering
    _SYMBOL = {DONE: "+", PRESENT: "=", MANUAL: "!", BLOCKED: "x"}

    def _lines(self, plan, applied: bool) -> list[str]:
        verb = "would change" if not applied else "changed"
        head = (
            f"'{plan.component.id}' is already fully present in {plan.agent_id} — nothing to do."
            if plan.nothing_to_do
            else f"{plan.component.title} — {len(plan.changes)} step(s) {verb} in "
            f"{plan.agent_id}'s ui/."
        )
        lines = [head, ""]
        for step in plan.steps:
            # The KIND is printed because one path legitimately appears twice: the SDK is both a
            # file to copy and a <script> tag to ensure. Two identical-looking lines with different
            # states reads as a bug in the report.
            lines.append(
                f"  {self._SYMBOL.get(step.state, '?')} {step.kind:<6} {step.target}"
                + (f" — {step.detail}" if step.detail else "")
            )
        return lines

    # ------------------------------------------------------------------ execute
    async def execute(self, tool_call_id, params, abort, on_update=None):
        agent_id = str(params.get("agent_id") or "").strip()
        component_id = str(params.get("component") or "").strip()
        if not agent_id or not component_id:
            return ToolResult.text(
                "add_ui_component needs an 'agent_id' and a 'component'.", is_error=True
            )

        try:
            plan = self._service.plan(
                agent_id, component_id, bool(params.get("confirm_overwrite"))
            )
        except ComponentError as e:
            # The service's message names the files at stake and the decision to make. Wrapping it
            # in a generic failure line would throw away the only actionable part.
            return ToolResult.text(str(e), is_error=True)

        dry_run = bool(params.get("dry_run"))
        if not dry_run and not plan.blocked:
            try:
                self._service.apply(plan)
            except ComponentError as e:
                return ToolResult.text(str(e), is_error=True)

        lines = self._lines(plan, applied=not dry_run and not plan.blocked)

        for step in plan.manual:
            lines += [
                "",
                f"PLACE THIS YOURSELF in ui/{step.target} — {step.detail}",
                "",
                step.payload,
            ]

        if plan.blocked:
            lines += [
                "",
                "NOTHING WAS WRITTEN. Resolve the 'x' rows above first.",
            ]
        elif not dry_run:
            missing = self._service.missing_sdk_symbols(plan)
            if missing:
                # Reported after applying, because applying refreshes the SDK — if this still
                # fires, the LIVE SDK genuinely lacks the symbol and rebuilding it is the fix.
                lines += [
                    "",
                    f"WARNING: the vendored SDK still has no {', '.join(missing)}. The app will "
                    "fail on load. Rebuild the SDK: `npm run build` in v2/clients/sdk-js (its "
                    "vendor step refreshes every agent's copy).",
                ]
            if plan.component.docs:
                lines += ["", plan.component.docs]
            if plan.changes:
                lines += [
                    "",
                    f"Then: `node --check` any .js that changed, and validate_agent('{agent_id}').",
                ]

        return ToolResult.text(
            "\n".join(lines),
            is_error=bool(plan.blocked),
            details={
                "agent_id": agent_id,
                "component": component_id,
                "dry_run": dry_run,
                "steps": [
                    {"kind": s.kind, "target": s.target, "state": s.state, "detail": s.detail}
                    for s in plan.steps
                ],
                "manual": [s.target for s in plan.manual],
                "blocked": [s.target for s in plan.blocked],
            },
        )

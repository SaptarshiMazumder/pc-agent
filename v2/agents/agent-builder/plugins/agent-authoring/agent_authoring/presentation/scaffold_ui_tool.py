"""scaffold_ui — start an agent's app from a working one, never from a blank file.

The result text is written for the MODEL, not for a log. It says what landed, that it already
works, which file to read next, and what NOT to touch — because the failure this tool exists
to prevent is not "no UI got written", it is "a UI got written that cannot possibly work".
A tool's description and its results are the model's entire picture of it.

The one thing this must never do is give the agent a second, subtly different chat view.
So: it copies, it says "now edit these words and these colours", and it points at the
template's own README for the rest.
"""

from __future__ import annotations

from agent_authoring.application.scaffold_ui_service import ScaffoldError
from agent_runtime.application.interfaces.tool import Tool, ToolResult


class ScaffoldUiTool(Tool):
    name = "scaffold_ui"
    label = "Scaffold App UI"
    default_retryable = False  # side-effecting (writes files); never auto-retry

    def __init__(self, service, templates):
        self._service = service
        self._templates = templates
        # Built from the catalogue rather than typed out, so a template added to the registry
        # is offered here automatically instead of existing but being un-nameable.
        self.description = (
            "START AN AGENT'S APP UI. Call this BEFORE writing any ui/ file — it copies a "
            "complete, working app into agents/<id>/ui/: streamed replies, live tool rows, "
            "image paste and drop, saved conversation history, and a settings page where the "
            "user pastes their own API key (BYOK). Then EDIT what it wrote.\n"
            "Writing ui/app.js by hand instead is the single most reliable way to ship a "
            "broken agent: the run-event payload is nested and streamed text is "
            "message_update/text_delta, and a view that gets either wrong connects, logs "
            "nothing, and never updates the screen. The template already has both right.\n"
            "It REFUSES if the agent already has a ui/ — ask the user before replacing "
            "someone's app.\n"
            "Templates:\n" + self._templates.describe() + "\n"
            "After scaffolding: read the README it wrote, edit the marked spots, then "
            "validate_agent. Remember the agent also needs an [app] table in its agent.toml "
            "(entry = 'ui/index.html') — this tool writes files, not configuration."
        )
        self.parameters = {
            "type": "object",
            "required": ["agent_id"],
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "the agent to give an app to (it must already exist)",
                },
                "template": {
                    "type": "string",
                    "enum": list(self._templates.ids()),
                    "description": "which starting app to copy (defaults to "
                    f"'{self._templates.default_id}')",
                },
                "confirm_overwrite": {
                    "type": "boolean",
                    "description": "REQUIRED when the agent already has a ui/. Set true ONLY "
                    "after the user has said to replace their existing app — it overwrites "
                    "files they may have edited by hand. Never set it on your own initiative",
                },
            },
        }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        agent_id = (params.get("agent_id") or "").strip()
        if not agent_id:
            return ToolResult.text("scaffold_ui needs an 'agent_id'", is_error=True)

        try:
            res = self._service.scaffold(
                agent_id,
                (params.get("template") or "").strip(),
                bool(params.get("confirm_overwrite")),
            )
        except ScaffoldError as e:
            # The service's message IS the answer — it names the files at stake and the
            # decision the user has to make. Wrapping it in a generic failure line would
            # throw away the only part the model can act on.
            return ToolResult.text(str(e), is_error=True)

        lines = [
            f"Scaffolded the '{res.template.id}' app into {res.ui_dir} — "
            f"{len(res.written)} files.",
            f"Wrote: {', '.join(res.written)}.",
        ]
        if res.replaced:
            lines.append(f"REPLACED (the previous app's files): {', '.join(res.replaced)}.")
        lines += [
            "",
            "THIS ALREADY WORKS. It connects, streams replies, shows tool calls, saves and "
            "reopens conversations, and lets the user set their own API key. Do not rewrite "
            "it — make it this agent's.",
            "",
            f"1. READ {res.readme_path} first. It names every spot to change and the two "
            f"protocol details that must not be touched.",
            "2. Edit the places marked CHANGE ME: the title and hero in index.html, BOT_NAME "
            "and hero() in chat.js, SUGGESTIONS in app.js, and --accent in style.css.",
            "3. Leave the event handling in chat.js alone. It is correct against the daemon "
            "and it is checked in CI.",
            "4. Add this agent's own surface (a button for its tool, a panel for its output) "
            "in app.js.",
            "",
            f"Then: make sure agent.toml has an [app] table with entry = 'ui/index.html', "
            f"run `node --check` on any .js you edited, and finish with "
            f"validate_agent('{res.agent_id}').",
        ]
        return ToolResult.text(
            "\n".join(lines),
            details={
                "agent_id": res.agent_id,
                "template": res.template.id,
                "written": res.written,
                "replaced": res.replaced,
                "readme": res.readme_path,
            },
        )

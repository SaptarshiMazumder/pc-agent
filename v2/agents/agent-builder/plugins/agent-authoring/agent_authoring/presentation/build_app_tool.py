"""build_app — compile an agent's app/ into the ui/ the daemon actually serves.

The description is written for the MODEL, and its job is to establish one fact that is invisible
from inside a conversation: editing app/src changes nothing anyone can see. The daemon serves ui/,
ui/ is build output, and a change that was never built is a change the user will look for and not
find — while every file they can inspect says the work was done.
"""

from __future__ import annotations

from agent_authoring.application.build_app_service import BuildAppError
from agent_runtime.application.interfaces.tool import Tool, ToolResult


class BuildAppTool(Tool):
    name = "build_app"
    label = "Build App"
    default_retryable = False  # side-effecting (writes ui/); never auto-retry
    description = (
        "BUILD an agent's window: compiles agents/<id>/app/ into agents/<id>/ui/ with vite.\n"
        "CALL THIS AFTER EVERY CHANGE TO app/. The daemon serves the BUILT ui/, never the "
        "source — so an edit to app/src that is not followed by a build is invisible to the "
        "user, who reloads the window and sees the old screen with nothing to explain it. This "
        "is also the last step before verify_app, package_agent or publish_agent: those read "
        "ui/, so an unbuilt change is one that does not ship.\n"
        "Dependencies are handled for you. The product ships one shared copy of react and vite, "
        "and this links the agent's app at it — no download, and it works with no network.\n"
        "NOT for an agent whose window is hand-written into ui/. That has nothing to compile and "
        "is live the moment it is saved; this tool will say so.\n"
        "On failure it returns vite's own error, naming the file and line. Fix that and call it "
        "again."
    )
    parameters = {
        "type": "object",
        "required": ["agent_id"],
        "properties": {
            "agent_id": {
                "type": "string",
                "description": "the agent whose app/ should be built (e.g. my-agent)",
            }
        },
    }

    def __init__(self, service):
        self._service = service

    async def execute(self, params: dict) -> ToolResult:
        agent_id = str(params.get("agent_id") or "").strip()
        if not agent_id:
            return ToolResult.text("build_app needs an agent_id.", is_error=True)

        try:
            result = self._service.build(agent_id)
        except BuildAppError as e:
            # The service's message is already written for a reader — it carries vite's own
            # output. Wrapping it in a verdict of our own would bury the only useful part.
            return ToolResult.text(str(e), is_error=True)

        # `details` is the structured channel, for a caller that wants the file list without
        # parsing prose; `content` is what the model reads.
        details = {
            "agent_id": result.agent_id,
            "dependencies": result.dependencies,
            "written": result.written,
        }
        how = {
            "linked": "dependencies: linked to the product's shared copy",
            "installed": "dependencies: installed from the network (no shared copy on this box)",
            "present": "dependencies: already in place",
        }.get(result.dependencies, f"dependencies: {result.dependencies}")

        return ToolResult.text(
            f"built {result.agent_id} -> ui/ ({len(result.written)} file(s))\n"
            f"{how}\n\n{result.output}\n\n"
            f"The window now serves this build. Reopen it to see the change.",
            details=details,
        )

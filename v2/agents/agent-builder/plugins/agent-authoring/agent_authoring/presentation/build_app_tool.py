"""build_app — compile an agent's app/ into the ui/ the daemon actually serves.

The description is written for the MODEL, and its job is to establish one fact that is invisible
from inside a conversation: editing app/src changes nothing anyone can see. The daemon serves ui/,
ui/ is build output, and a change that was never built is a change the user will look for and not
find — while every file they can inspect says the work was done.
"""

from __future__ import annotations

import asyncio

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

    def __init__(self, service, announce=None):
        """:param announce: ``callable(agent_id)`` telling that agent's open windows to reload.

        OPTIONAL, AND BEST-EFFORT. It is absent in unit tests and on any build of the daemon whose
        gateway has not come up yet, and a build that failed because nothing was listening would
        be a worse outcome than a window that did not refresh."""
        self._service = service
        self._announce = announce

    async def execute(self, tool_call_id, params: dict, abort=None, on_update=None) -> ToolResult:
        # THE RUNTIME'S CALLING CONVENTION, not a convenience one. The engine invokes every tool
        # as `execute(call.id, args, abort, on_update)`; this took `(params)` alone, so every
        # build_app call — the model's, and the window's Open App button — died with a TypeError
        # before the build even started. The unit tests passed because they called the method
        # directly with the shape it happened to have, which is exactly how a signature drifts.

        agent_id = str(params.get("agent_id") or "").strip()
        if not agent_id:
            return ToolResult.text("build_app needs an agent_id.", is_error=True)

        try:
            # OFF THE EVENT LOOP. The daemon is single-threaded — one thread carries every
            # websocket, every model stream, every agent's run and every tool. A vite build takes tens of seconds, and a
            # plain call here stopped the ENTIRE daemon for that long: no events, no replies,
            # not even an answer to the client's keepalive. Windows concluded the connection
            # was dead, reconnected, and told people "the daemon restarted mid-run" when it
            # had done nothing of the sort.
            #
            # A thread is the whole fix, and `verify_app` already does this for the same
            # reason: one blocking operation with nothing to interleave.
            result = await asyncio.to_thread(self._service.build, agent_id)
        except BuildAppError as e:
            # The service's message is already written for a reader — it carries vite's own
            # output. Wrapping it in a verdict of our own would bury the only useful part.
            return ToolResult.text(str(e), is_error=True)

        # ON SUCCESS ONLY, and this is the whole reason the call sits after the error return
        # above rather than in a `finally`. A failed build leaves `ui/` exactly as it was, so
        # reloading would repaint the same screen -- which reads as "my change did nothing" when
        # what actually happened is that it did not compile.
        if self._announce is not None:
            self._announce(result.agent_id)

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
            f"The window now serves this build. Any window of this agent that is already open "
            f"reloads itself; opening one shows this build.",
            details=details,
        )

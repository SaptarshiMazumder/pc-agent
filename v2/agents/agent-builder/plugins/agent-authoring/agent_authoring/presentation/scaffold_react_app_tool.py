"""scaffold_react_app — a buildable React project, and nothing else.

The result text is written for the MODEL. It says what landed, what deliberately did NOT land,
and where to look before writing a line of it — because the failure this tool exists to prevent
is not "no app got written", it is "an app got written from priors instead of from the working
agents sitting in this product".
"""

from __future__ import annotations

from agent_authoring.application.scaffold_react_app_service import ReactScaffoldError
from agent_runtime.application.interfaces.tool import Tool, ToolResult


class ScaffoldReactAppTool(Tool):
    name = "scaffold_react_app"
    label = "Scaffold React App"
    default_retryable = False  # side-effecting (writes files); never auto-retry
    description = (
        "GIVE AN AGENT A WINDOW: writes agents/<id>/app/ with package.json, vite.config.ts, "
        "tsconfig.json, index.html and the SDK vendored inside it. It writes NO components and "
        "only the mandatory src/ files — you write the rest, after reading the sample agents.\n"
        "THIS IS HOW A WINDOW IS MADE. There is one way, so there is no wrong one to choose: "
        "source in app/, compiled into ui/ by build_app, with the toolchain shipped in the "
        "product so the user has nothing to install.\n"
        "THE SDK IS VENDORED, not a dependency. Do NOT add '@agentd/client' to package.json: it "
        "resolves only inside this product's own repo, so an agent that declares it fails at "
        "npm install on every machine except the author's. The alias in vite.config.ts and the "
        "`paths` entry in tsconfig.json already provide it.\n"
        "It REFUSES if the agent already has an app/ — ask the user before replacing someone's "
        "work.\n"
        "After scaffolding: read the README it wrote, read the agents under agents/samples/ to "
        "decide what this window should be, write src/, then `npm install && npm run build` in "
        "app/ (the daemon serves the BUILT ui/, so an unbuilt change is invisible), and set "
        "[app] entry = 'ui/index.html' in agent.toml — this tool writes files, not configuration."
    )
    parameters = {
        "type": "object",
        "required": ["agent_id"],
        "properties": {
            "agent_id": {
                "type": "string",
                "description": "the agent to give an app to (it must already exist)",
            },
            "confirm_overwrite": {
                "type": "boolean",
                "description": "REQUIRED when the agent already has an app/. Set true ONLY "
                "after the user has said to replace their existing project — it overwrites "
                "files they may have edited by hand. Never set it on your own initiative",
            },
        },
    }

    def __init__(self, service):
        self._service = service

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            result = self._service.scaffold(
                agent_id=str(params.get("agent_id") or "").strip(),
                confirm_overwrite=bool(params.get("confirm_overwrite")),
            )
        except ReactScaffoldError as e:
            return ToolResult.text(str(e), is_error=True)

        return ToolResult.text(
            f"Wrote a React project into {result.app_dir} "
            f"({len(result.written)} files):\n"
            + "\n".join(f"  app/{rel}" for rel in result.written)
            + "\n\nNo src/ — that is deliberate. What this window should BE is a judgement "
            "about this agent, and the material for it is the working agents under "
            "agents/samples/. Read MORE THAN ONE: they overlap where the platform has a right "
            "answer and differ where the product does, and the differences are the part worth "
            "thinking about. Take the mechanism, not the layout, and build what this agent "
            "needs — including things no sample has.\n"
            f"Read {result.readme_path} first; it explains the vendored SDK and the app/ -> ui/ "
            "build, both of which are easy to break in ways that only fail on someone else's "
            "machine."
        )

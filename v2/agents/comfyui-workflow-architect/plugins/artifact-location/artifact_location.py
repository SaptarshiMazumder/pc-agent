"""Agent-authored tool (created at runtime by create_tool). Edit with care."""

from agent_runtime.application.interfaces.tool import Tool, ToolResult


class GeneratedTool(Tool):
    name = 'open_artifact_location'
    label = 'Open Artifact Location'
    default_retryable = False
    description = "Reveal an artifact file from this agent's workspace in the desktop file manager. Use only when the user clicks Open location in the app."
    parameters = {'type': 'object', 'required': ['path'], 'properties': {'path': {'type': 'string', 'description': 'Workspace-relative artifact path.'}}}

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            import os
            import subprocess
            import sys
            from pathlib import Path

            raw = str(params.get("path", "")).strip().replace("\\", "/")
            if not raw:
                return ToolResult.text("path is required", is_error=True)

            # The tool module lives at agents/<id>/plugins/artifact-location/<module>.py.
            workspace = Path(__file__).resolve().parents[2] / "workspace"
            target = (workspace / raw).resolve()
            try:
                target.relative_to(workspace.resolve())
            except ValueError:
                return ToolResult.text("refusing to open a path outside this agent's workspace", is_error=True)

            if not target.exists() or not target.is_file():
                return ToolResult.text(f"artifact not found: {raw}", is_error=True)

            try:
                if sys.platform.startswith("win"):
                    subprocess.Popen(["explorer.exe", "/select,", str(target)])
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", "-R", str(target)])
                else:
                    subprocess.Popen(["xdg-open", str(target.parent)])
            except Exception as exc:
                return ToolResult.text(f"could not open file location: {exc}", is_error=True)

            return ToolResult.text(f"opened file location for {raw}")
        except Exception as e:  # noqa: BLE001 — never let an authored tool crash the loop
            return ToolResult.text(f"open_artifact_location failed: {type(e).__name__}: {e}", is_error=True)


def register(api, ctx):
    api.register_tool(GeneratedTool())

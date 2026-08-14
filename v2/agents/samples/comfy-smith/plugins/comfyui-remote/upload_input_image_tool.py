"""upload_input_image — put a local image onto the remote server so a node can load it.

The server is somewhere else, which means a LoadImage node cannot reach a file on this machine.
Anything the user drops into the chat — a reference photo, a broken render, a mask, a ControlNet
input — has to be uploaded to that box first, and the name this returns is the exact string
LoadImage's `image` input expects.

Without this, img2img / inpainting / ControlNet are impossible against a remote ComfyUI, and the
agent would have to tell the user to upload files by hand.
"""

from __future__ import annotations

from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from comfy_client import ComfyClient, ComfyError

#: Guard against handing a multi-gigabyte file to an HTTP upload — the failure that produces is
#: a timeout with no explanation, which is the worst kind.
MAX_BYTES = 100 * 1024 * 1024


class UploadInputImageTool(Tool):
    name = "upload_input_image"
    label = "Upload Input Image"
    default_retryable = False
    description = (
        "Upload a local image to the ComfyUI server's input folder and get back the exact "
        "filename a LoadImage node should use. REQUIRED before any img2img, inpainting or "
        "ControlNet workflow — the server is a different machine and cannot read paths on this "
        "one. Use it for images the user attaches to the conversation."
    )
    parameters = {
        "type": "object",
        "required": ["path"],
        "properties": {
            "path": {"type": "string", "description": "Path to the local image file."},
            "subfolder": {
                "type": "string",
                "description": "Optional subfolder inside the server's input directory.",
            },
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        path = Path(str(params.get("path") or "").strip())
        subfolder = str(params.get("subfolder") or "").strip()
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            return ToolResult.text(f"no file at {path}", is_error=True)
        except OSError as e:
            return ToolResult.text(f"could not read {path}: {e}", is_error=True)

        if len(data) > MAX_BYTES:
            return ToolResult.text(
                f"{path.name} is {len(data) / 1024 / 1024:.0f}MB, over the {MAX_BYTES // 1024 // 1024}MB "
                f"upload limit. Resize it first.",
                is_error=True,
            )

        try:
            client = ComfyClient.from_settings()
            result = await client.upload_image(path.name, data, subfolder)
        except ComfyError as e:
            return ToolResult.text(str(e), is_error=True)

        name = result.get("name") or path.name
        where = result.get("subfolder") or ""
        # LoadImage addresses a subfolder as part of the name, not as a separate input.
        reference = f"{where}/{name}" if where else name
        return ToolResult.text(
            f"uploaded to {client.base} ({len(data) / 1024:.0f}KB).\n"
            f'Use it as: "image": "{reference}" in a LoadImage node.'
        )

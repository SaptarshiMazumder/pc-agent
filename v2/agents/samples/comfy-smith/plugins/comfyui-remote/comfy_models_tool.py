"""comfy_models — the weights that are actually on that machine.

WHY THIS IS NOT A SETTING. "Where are your models?" is a question about a folder on a box the
agent has never seen, answered by a user reading a path off a rented pod. The server knows. Ask
it, and the answer is always current — including after the user downloads something mid-session.

The names returned here are the EXACT strings a loader node wants. A checkpoint referenced by a
name that is off by a version suffix fails at queue time with "value not in list", which is one
of the most common ways a generated workflow dies.
"""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from comfy_client import ComfyClient, ComfyError


class ComfyModelsTool(Tool):
    name = "comfy_models"
    label = "Comfy Models"
    default_retryable = True
    description = (
        "What model files exist on the ComfyUI server. With no arguments, lists the model "
        "folders (checkpoints, loras, vae, controlnet, upscale_models, …). With `folder`, lists "
        "the exact filenames in it. Use the names verbatim in loader nodes — a checkpoint name "
        "that is not on this list is rejected at queue time. If what the user wants is missing, "
        "say so and offer to download it over SSH rather than writing it into the workflow."
    )
    parameters = {
        "type": "object",
        "properties": {
            "folder": {
                "type": "string",
                "description": "A model folder, e.g. 'checkpoints', 'loras', 'vae'.",
            },
            "search": {
                "type": "string",
                "description": "Substring filter applied to the filenames in `folder`.",
            },
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        folder = str(params.get("folder") or "").strip()
        search = str(params.get("search") or "").strip().lower()

        try:
            client = ComfyClient.from_settings()
            listing = await client.get_json(f"/models/{folder}" if folder else "/models")
        except ComfyError as e:
            # A 404 here means an older ComfyUI without the /models endpoint — the loader node's
            # own enum carries the same list, so the agent is told exactly where to go instead.
            hint = (
                "\n\nThis server may predate the /models endpoint. The same list is in the "
                "loader node's input enum: `comfy_nodes` with node='CheckpointLoaderSimple' "
                "(or LoraLoader / VAELoader)."
                if "404" in str(e)
                else ""
            )
            return ToolResult.text(f"{e}{hint}", is_error=True)

        names = [str(x) for x in listing] if isinstance(listing, list) else []
        if not folder:
            return ToolResult.text(
                f"Model folders on {client.base}:\n"
                + "\n".join(f"- {n}" for n in sorted(names))
                + "\n\nAsk for one with `folder` to see its files."
            )

        if search:
            names = [n for n in names if search in n.lower()]
        if not names:
            return ToolResult.text(
                f"Nothing in {folder!r} on {client.base}"
                + (f" matching {search!r}" if search else "")
                + ". Do not reference a file that is not here — either pick from what exists or "
                "download it onto that machine first."
            )
        shown = sorted(names)[:200]
        body = "\n".join(f"- {n}" for n in shown)
        more = f"\n… and {len(names) - 200} more" if len(names) > 200 else ""
        return ToolResult.text(f"{len(names)} file(s) in {folder}:\n{body}{more}")

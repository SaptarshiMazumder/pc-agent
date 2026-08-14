"""comfy_server — what the machine on the other end of COMFY_URL actually is.

THIS TOOL REPLACES A CONVERSATION. The agent used to open by asking "how much VRAM do you have?"
and "where do your models live?" — questions whose answers the server already knows, and whose
answers the user gets wrong. VRAM is not what the card is sold as; it is what is FREE right now,
after whatever is already loaded. One call, and the architecture decision (model size, precision,
tiling, batch) rests on a fact.
"""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from comfy_client import ComfyClient, ComfyError, ssh_target


class ComfyServerTool(Tool):
    name = "comfy_server"
    label = "Comfy Server"
    default_retryable = True
    description = (
        "The ComfyUI server this agent is pointed at: whether it is reachable, the GPU and how "
        "much VRAM is FREE right now, the ComfyUI version, and how busy the queue is. CALL THIS "
        "FIRST, before designing anything — free VRAM decides model size and precision, and it "
        "is never what the user thinks. Also reports the SSH target if one is configured, for "
        "when a job needs a custom node or a model that is not installed yet."
    )
    parameters = {"type": "object", "properties": {}}

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            client = ComfyClient.from_settings()
            stats = await client.get_json("/system_stats")
            queue = await client.get_json("/queue")
        except ComfyError as e:
            return ToolResult.text(str(e), is_error=True)

        lines = [f"ComfyUI at {client.base} — reachable"]

        system = stats.get("system") or {}
        version = system.get("comfyui_version") or "unknown"
        lines.append(
            f"ComfyUI {version} · python {system.get('python_version', '?').split()[0]} "
            f"· torch {system.get('pytorch_version', '?')}"
        )
        if system.get("ram_total"):
            lines.append(
                f"System RAM: {_gb(system.get('ram_free'))} free of {_gb(system.get('ram_total'))}"
            )

        devices = stats.get("devices") or []
        if not devices:
            # A ComfyUI with no device is running on CPU — worth saying loudly, because every
            # model recommendation below it changes.
            lines.append("No GPU reported — this server is on CPU. Generation will be very slow.")
        for d in devices:
            lines.append(
                f"GPU: {d.get('name', '?')} — {_gb(d.get('vram_free'))} VRAM free of "
                f"{_gb(d.get('vram_total'))} (torch holds {_gb(d.get('torch_vram_total'))})"
            )

        running = len(queue.get("queue_running") or [])
        pending = len(queue.get("queue_pending") or [])
        lines.append(f"Queue: {running} running, {pending} pending")

        target = ssh_target()
        lines.append(
            f"SSH: {target} — use `exec` with "
            f"`ssh {target} \"<command>\"` to install custom nodes or download models"
            if target
            else "SSH: not configured. Anything needing a shell on that box (installing a custom "
            "node, downloading a checkpoint) has to be done by the user, or they can set "
            "COMFY_SSH in Settings."
        )
        return ToolResult.text("\n".join(lines))


def _gb(value) -> str:
    try:
        return f"{int(value) / (1024**3):.1f}GB"
    except (TypeError, ValueError):
        return "?"

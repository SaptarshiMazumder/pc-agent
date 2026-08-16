"""run_workflow — queue a workflow on the remote ComfyUI, watch it, and bring the images back.

THIS IS THE TOOL THAT MAKES THE AGENT HONEST.

Everything else it can do is a claim. It can research a model, name the nodes, write a graph and
validate its structure — and still be wrong, because a graph that is structurally perfect can
still ask for a LoRA that is not installed, or a resolution that does not fit in the free VRAM,
or a sampler this build renamed. Only the server can settle it, and the loop that makes an agent
competent is write -> RUN -> read the real failure -> fix. Without this tool that loop is
missing its middle step and the agent is guessing with confidence.

WHAT IT REPORTS WHILE IT WAITS. A generation is thirty seconds to ten minutes. The websocket
carries per-node progress, so `on_update` forwards "KSampler 7/20" as it happens — otherwise the
user watches a motionless row and cannot tell rendering from hung.

THE API FORMAT, NOT THE UI EXPORT. `/prompt` takes the flat `{id: {class_type, inputs}}` form.
Handing it a canvas export produces a confusing rejection, so that is caught here with the fix
spelled out rather than passed through.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import aiohttp

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_workspace

from comfy_client import ComfyClient, ComfyError

#: A cold model load plus a video generation can genuinely take this long. The agent can override.
DEFAULT_TIMEOUT_S = 900


class RunWorkflowTool(Tool):
    name = "run_workflow"
    label = "Run Workflow"
    concurrency = "sequential"  # one generation at a time; the GPU is a single resource
    default_retryable = False
    description = (
        "Queue a workflow (API format) on the ComfyUI server, wait for it, and download the "
        "images it produced into the workspace. RUN EVERY WORKFLOW BEFORE PRESENTING IT — a "
        "graph that validates can still fail on a missing model, a renamed sampler or VRAM, and "
        "this is the only tool that finds that out. On failure it returns the server's own node "
        "error verbatim: read it, fix the graph, and run it again."
    )
    parameters = {
        "type": "object",
        "required": ["path"],
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the workflow JSON, in API format ({id: {class_type, inputs}}).",
            },
            "timeout_s": {
                "type": "integer",
                "description": f"How long to wait for the run. Default {DEFAULT_TIMEOUT_S}.",
            },
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        raw = str(params.get("path") or "").strip()
        timeout_s = int(params.get("timeout_s") or DEFAULT_TIMEOUT_S)
        path = Path(raw)
        try:
            graph = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return ToolResult.text(f"no workflow at {path}", is_error=True)
        except json.JSONDecodeError as e:
            return ToolResult.text(
                f"{path.name} is not valid JSON (line {e.lineno}, column {e.colno}): {e.msg}",
                is_error=True,
            )
        except OSError as e:
            return ToolResult.text(f"could not read {path}: {e}", is_error=True)

        if not isinstance(graph, dict):
            return ToolResult.text("the workflow root must be a JSON object", is_error=True)
        if isinstance(graph.get("nodes"), list):
            return ToolResult.text(
                f"{path.name} is a UI export (it has a `nodes` array). /prompt only accepts the "
                f"API format: a flat object of id -> {{class_type, inputs}}. Write the API-format "
                f"file and run that; keep the UI export too if the user wants to open it in the "
                f"canvas.",
                is_error=True,
            )

        try:
            client = ComfyClient.from_settings()
        except ComfyError as e:
            return ToolResult.text(str(e), is_error=True)

        client_id = uuid.uuid4().hex
        try:
            return await self._run(client, client_id, graph, path, timeout_s, abort, on_update)
        except ComfyError as e:
            return ToolResult.text(str(e), is_error=True)

    async def _run(self, client, client_id, graph, path, timeout_s, abort, on_update):
        say = on_update or (lambda _text: None)
        timeout = aiohttp.ClientTimeout(total=None, sock_read=timeout_s)
        async with aiohttp.ClientSession(timeout=timeout, headers=client.headers) as session:
            # THE SOCKET OPENS FIRST. Queue the prompt before connecting and a short job can
            # finish before the socket is up — the run succeeds and this tool waits forever for
            # events that already went out.
            try:
                ws = await session.ws_connect(client.ws_url(client_id))
            except aiohttp.ClientError as e:
                raise ComfyError(
                    f"could not open the ComfyUI event socket at {client.base} ({e}). "
                    f"Some proxies allow HTTP but block websockets."
                ) from e

            async with ws:
                queued = await client.post_json(
                    "/prompt", {"prompt": graph, "client_id": client_id}
                )
                prompt_id = str(queued.get("prompt_id") or "")
                node_errors = queued.get("node_errors") or {}
                if node_errors:
                    # ComfyUI validated the graph and refused parts of it. This is the most
                    # actionable failure there is — every entry names the node and the input.
                    return ToolResult.text(
                        f"{path.name} was REJECTED by {client.base}:\n"
                        + json.dumps(node_errors, indent=2)[:4000],
                        is_error=True,
                    )
                if not prompt_id:
                    raise ComfyError(f"/prompt returned no prompt_id: {queued!r}")

                say(f"queued {path.name} ({len(graph)} nodes) — waiting for the GPU")
                outcome = await self._watch(ws, prompt_id, graph, timeout_s, abort, say)

            if outcome.get("aborted"):
                # Stop the GPU too — abandoning the socket would leave the job running and the
                # next request queued behind it.
                await client.post_json("/interrupt", {"prompt_id": prompt_id})
                return ToolResult.text(f"run cancelled; asked {client.base} to interrupt it")

            if outcome.get("error"):
                err = outcome["error"]
                return ToolResult.text(
                    f"{path.name} FAILED on {client.base}\n"
                    f"node {err.get('node_id')} ({err.get('node_type')}): "
                    f"{err.get('exception_type')}: {err.get('exception_message')}\n"
                    f"{_tail(err.get('traceback'))}",
                    is_error=True,
                )

        images = await self._collect(client, prompt_id)
        if not images:
            return ToolResult.text(
                f"{path.name} ran without error but produced no images. The graph probably has "
                f"no output node (SaveImage / PreviewImage / VHS_VideoCombine) — check with "
                f"comfy_nodes which of its nodes has output_node set.",
                is_error=True,
            )

        saved = await self._download(client, images, say)
        names = "\n".join(f"- {p}" for p in saved)
        return ToolResult.text(
            f"{path.name} ran on {client.base} and produced {len(saved)} file(s):\n{names}",
            artifacts=saved,
        )

    @staticmethod
    async def _watch(ws, prompt_id, graph, timeout_s, abort, say) -> dict:
        """Consume the event socket until this prompt finishes, fails, or time runs out."""
        deadline = asyncio.get_running_loop().time() + timeout_s
        current_node = ""
        while True:
            if abort.is_set():
                return {"aborted": True}
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise ComfyError(
                    f"the run did not finish within {timeout_s}s. It may still be going on the "
                    f"server — check the queue with comfy_server, or pass a larger timeout_s."
                )
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=min(remaining, 30))
            except TimeoutError:
                # A quiet socket is normal while a big model loads. Say so, so the row moves.
                say(f"still running ({int(deadline - asyncio.get_running_loop().time())}s left)")
                continue

            if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                raise ComfyError("the ComfyUI event socket closed mid-run")
            if msg.type is not aiohttp.WSMsgType.TEXT:
                continue  # binary frames are live previews — nothing to report

            event = json.loads(msg.data)
            kind, data = event.get("type"), event.get("data") or {}
            if data.get("prompt_id") and data["prompt_id"] != prompt_id:
                continue  # somebody else's job on a shared server

            if kind == "executing":
                node = data.get("node")
                if node is None and data.get("prompt_id") == prompt_id:
                    return {"done": True}
                current_node = _label(graph, node)
                say(f"running {current_node}")
            elif kind == "progress":
                value, total = data.get("value"), data.get("max")
                label = _label(graph, data.get("node")) or current_node
                say(f"{label} {value}/{total}")
            elif kind == "execution_error":
                return {"error": data}
            elif kind == "execution_cached" and data.get("nodes"):
                say(f"{len(data['nodes'])} node(s) reused from cache")

    @staticmethod
    async def _collect(client, prompt_id) -> list[dict]:
        history = await client.get_json(f"/history/{prompt_id}")
        record = (history or {}).get(prompt_id) or {}
        out: list[dict] = []
        for node_output in (record.get("outputs") or {}).values():
            for key in ("images", "gifs", "videos", "audio"):
                for item in node_output.get(key) or []:
                    if item.get("filename"):
                        out.append(item)
        return out

    @staticmethod
    async def _download(client, images, say) -> list[str]:
        dest = Path(current_workspace(".")) / "outputs"
        dest.mkdir(parents=True, exist_ok=True)
        saved: list[str] = []
        for i, item in enumerate(images, 1):
            say(f"downloading {i}/{len(images)}: {item['filename']}")
            data = await client.get_bytes(
                "/view",
                {
                    "filename": item["filename"],
                    "subfolder": item.get("subfolder", ""),
                    "type": item.get("type", "output"),
                },
            )
            target = dest / item["filename"]
            target.write_bytes(data)
            saved.append(str(target))
        return saved


def _label(graph: dict, node_id) -> str:
    """`KSampler (node 3)` rather than `3` — the bare id means nothing to the person watching."""
    if node_id is None:
        return ""
    spec = graph.get(str(node_id)) or {}
    title = (spec.get("_meta") or {}).get("title")
    return f"{title or spec.get('class_type') or 'node'} (node {node_id})"


def _tail(traceback) -> str:
    if not traceback:
        return ""
    lines = traceback if isinstance(traceback, list) else str(traceback).splitlines()
    return "\n".join(str(line).rstrip() for line in lines[-12:])

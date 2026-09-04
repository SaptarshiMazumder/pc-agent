"""ComfyUI bridge — the agent's only route to a running instance.

EVERY REQUEST GOES THROUGH THE HOST. `fetch` is the brokered call: this module never opens a
socket, never reads an environment variable, never spawns anything. The URL is written as
`${COMFYUI_URL}/api/...` and the credential as `${COMFYUI_AUTH}`; the host substitutes both from
the CALLER's settings at the last moment. That is what lets one copy of this agent serve several
people pointing at different boxes, and it is why none of these values appear in this file.
(`comfy_research`, in its own module, uses the same brokered `fetch` to reach Hugging Face and
Civitai — the network is open for plugins; the instance's address is still per caller.)

THE `/api` PREFIX IS DELIBERATE. ComfyUI registers every route twice — `/prompt` and
`/api/prompt` — and hosted proxies (vast's portal, RunPod, Modal) route on `/api/*` while
serving the web app at `/`. The unprefixed form collides with that; the prefixed one works in
both places.

IMAGE BYTES CROSS EXACTLY TWICE, both through the host's file lanes: `comfy_upload` sends a
workspace file up as multipart, and `comfy_download` streams a rendered output down to the
workspace (fetch's `save_path`), where it becomes an artifact the chat renders. The model still
never receives the pixels — describing a picture stays the user's job; showing it is now ours.
"""

from __future__ import annotations

import json
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_workspace
from agent_runtime.infrastructure.net.outbound import fetch

#: What a model file looks like in a loader's enum. The DETECTION is generic on purpose — the
#: previous version of this tool was a hardcoded list of seven loaders, which made every model
#: family that loads differently (Flux and friends live in unet/ behind UNETLoader, not
#: CheckpointLoaderSimple) simply invisible: a Flux-only instance reported "no models
#: installed". Matching by what the VALUES look like means a loader from a custom pack
#: installed five minutes ago is found the same way the stock ones are.
_MODEL_EXTS = (".safetensors", ".sft", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".onnx")


def _looks_like_model_list(values) -> bool:
    """An enum whose entries are model FILENAMES, as opposed to sampler names or booleans."""
    if not isinstance(values, list) or not values:
        return False
    names = [v for v in values if isinstance(v, str)]
    if not names:
        return False
    hits = sum(1 for v in names if v.lower().endswith(_MODEL_EXTS))
    # Most, not all: some packs mix a "None" sentinel or a .yaml config into the list.
    return hits >= max(1, len(names) // 2)


def _model_enums(catalogue: dict):
    """Every (node_class, input_name, filenames) in one `/object_info` payload.

    The catalogue's shape: {class: {"input": {"required": {name: [spec, ...]}, "optional":
    {...}}}}. An enum input's spec is a nested list of its legal values; a plain socket's is a
    type string — only the first shape can hold filenames.
    """
    for node_class, spec in (catalogue or {}).items():
        if not isinstance(spec, dict):
            continue
        inputs = spec.get("input") or {}
        for section in ("required", "optional"):
            for input_name, entry in (inputs.get(section) or {}).items():
                values = entry[0] if isinstance(entry, list) and entry else None
                if _looks_like_model_list(values):
                    yield node_class, input_name, [v for v in values if isinstance(v, str)]


def _headers() -> dict:
    """The credential headers, as PLACEHOLDERS.

    Written unconditionally because this code cannot see whether a setting is filled in — and
    must not, that is the point. An unset one substitutes to nothing the host can use and the
    request goes out without it.
    """
    return {
        "Authorization": "${COMFYUI_AUTH}",
        "Modal-Key": "${COMFYUI_AUTH2}",
        "Modal-Secret": "${COMFYUI_AUTH3}",
    }


def _get(path: str, timeout_s: float = 30.0):
    return fetch(f"${{COMFYUI_URL}}{path}", headers=_headers(), timeout_s=timeout_s)


def _post(path: str, body, timeout_s: float = 60.0):
    return fetch(
        f"${{COMFYUI_URL}}{path}", method="POST", json=body, headers=_headers(), timeout_s=timeout_s
    )


def _failed(res, what: str) -> str:
    """One sentence naming what went wrong, in the server's own words where there are any.

    A transport failure and a 401 need different fixes, and "could not reach ComfyUI" hides
    which one happened.
    """
    if res.error:
        return (
            f"{what}: could not reach the instance ({res.error}). Check COMFYUI_URL in this "
            f"agent's settings, and that the box is running."
        )
    if res.status in (401, 403):
        return (
            f"{what}: the instance refused the credential (HTTP {res.status}). Set "
            f"COMFYUI_AUTH to the whole header value — 'Bearer …' or 'Basic …'."
        )
    return f"{what}: HTTP {res.status} — {(res.text or '')[:300]}"


class ComfyProbeTool(Tool):
    name = "comfy_probe"
    label = "Probe ComfyUI"
    default_retryable = True
    description = (
        "Check the configured ComfyUI instance: reachable, credential accepted, what version it "
        "runs and how much VRAM it has. Call this FIRST in any session that will touch ComfyUI — "
        "everything else assumes it answered."
    )
    parameters = {"type": "object", "properties": {}}

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            res = _get("/api/system_stats")
            if not res.ok:
                return ToolResult.text(_failed(res, "probe"), is_error=True)
            data = res.json()
            system = data.get("system") or {}
            devices = data.get("devices") or []
            lines = [
                f"ComfyUI {system.get('comfyui_version') or 'unknown'} "
                f"(python {system.get('python_version', '?').split()[0]}, "
                f"torch {system.get('pytorch_version') or '?'})"
            ]
            for d in devices:
                free = int(d.get("vram_free") or 0) // (1024**3)
                total = int(d.get("vram_total") or 0) // (1024**3)
                lines.append(f"{d.get('name') or d.get('type')}: {free} GB free of {total} GB")
            queue = _get("/api/prompt")
            if queue.ok:
                try:
                    n = (queue.json().get("exec_info") or {}).get("queue_remaining")
                    lines.append(f"queue: {n} waiting")
                except ValueError:
                    pass
            # Telemetry for the window's Studio dashboard — never load-bearing for the turn.
            import studio_state

            first = devices[0] if devices else {}
            studio_state.set_instance(
                version=system.get("comfyui_version"),
                gpu=first.get("name") or first.get("type"),
                vram_free=first.get("vram_free"),
                vram_total=first.get("vram_total"),
            )
            return ToolResult.text("\n".join(lines), details=data)
        except Exception as e:  # noqa: BLE001 — a tool reports, it does not crash the turn
            return ToolResult.text(f"comfy_probe failed: {type(e).__name__}: {e}", is_error=True)


class ComfyInventoryTool(Tool):
    name = "comfy_inventory"
    label = "ComfyUI inventory"
    default_retryable = True
    description = (
        "Every model file this instance can load, found by reading the FULL node catalogue and "
        "collecting each input whose legal values are model filenames — so loaders from custom "
        "packs (UNETLoader for Flux-family, GGUF loaders, whatever exists) are covered, not just "
        "the stock ones. Read this BEFORE designing and use only names it returns. Samplers, "
        "schedulers and other non-file enums: read comfy_node_spec on the node that owns them."
    )
    parameters = {
        "type": "object",
        "properties": {
            "filter": {
                "type": "string",
                "description": "Case-insensitive substring to narrow the report, e.g. 'flux' "
                "or 'lora'. Omit for everything.",
            }
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            res = _get("/api/object_info", timeout_s=60.0)
            if not res.ok:
                return ToolResult.text(_failed(res, "inventory"), is_error=True)
            try:
                catalogue = res.json()
            except ValueError:
                # The one honest failure of reading everything at once: a node-heavy install's
                # catalogue can exceed the fetch byte cap and arrive truncated. Say so — a
                # per-class comfy_node_spec still works, and the operator can raise the cap.
                return ToolResult.text(
                    "the instance's node catalogue is too large to fetch whole (truncated "
                    "mid-JSON). Read specific loaders with comfy_node_spec, or raise "
                    "sandbox_fetch_limits.max_bytes in the daemon config.",
                    is_error=True,
                )

            needle = str(params.get("filter") or "").strip().lower()
            found: dict = {}
            for node_class, input_name, files in _model_enums(catalogue):
                if needle and not (
                    needle in node_class.lower()
                    or needle in input_name.lower()
                    or any(needle in f.lower() for f in files)
                ):
                    continue
                key = f"{node_class}.{input_name}"
                found[key] = (
                    [f for f in files if needle in f.lower()] if needle else files
                ) or files

            if not found:
                return ToolResult.text(
                    ("nothing matching " + repr(needle) if needle else "no model files")
                    + " — no loader on this instance lists any. If models were just added, "
                    "ComfyUI only rescans its folders on restart or via its Refresh button.",
                    details={},
                )
            lines = [f"{len(found)} loader input(s) with model files:"]
            for key in sorted(found):
                items = found[key]
                shown = ", ".join(items[:10])
                more = f" … and {len(items) - 10} more" if len(items) > 10 else ""
                lines.append(f"{key} ({len(items)}): {shown}{more}")
            if not needle:  # a filtered view is a subset — never record it as the whole
                import studio_state

                studio_state.set_instance(
                    models=[
                        {"loader": key, "name": name}
                        for key in sorted(found)
                        for name in found[key]
                    ][:200]
                )
            return ToolResult.text("\n".join(lines), details=found)
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(
                f"comfy_inventory failed: {type(e).__name__}: {e}", is_error=True
            )


class ComfyNodeSpecTool(Tool):
    name = "comfy_node_spec"
    label = "ComfyUI node spec"
    default_retryable = True
    description = (
        "The exact inputs of ONE node class on this instance — names, types, defaults and the "
        "permitted values of every enum. Use it before wiring a node you have not used here, "
        "and to find out why an input was rejected."
    )
    parameters = {
        "type": "object",
        "required": ["node_class"],
        "properties": {
            "node_class": {
                "type": "string",
                "description": "Exact class name, e.g. 'KSampler' or 'CheckpointLoaderSimple'.",
            }
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            node_class = str(params.get("node_class") or "").strip()
            if not node_class:
                return ToolResult.text("node_class is required", is_error=True)
            res = _get(f"/api/object_info/{node_class}")
            if not res.ok:
                return ToolResult.text(_failed(res, node_class), is_error=True)
            body = res.json()
            spec = body.get(node_class)
            if not spec:
                return ToolResult.text(
                    f"'{node_class}' is not installed on this instance. comfy_inventory shows "
                    f"what is; a missing class usually means a custom node pack is not there.",
                    is_error=True,
                )
            return ToolResult.text(json.dumps(spec, indent=2)[:4000], details=spec)
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(
                f"comfy_node_spec failed: {type(e).__name__}: {e}", is_error=True
            )


class ComfyUploadTool(Tool):
    name = "comfy_upload"
    label = "Upload images to ComfyUI"
    default_retryable = True
    description = (
        "Push image files from this run's workspace to the ComfyUI instance's input folder, so "
        "a LoadImage node can use them. Images the user attaches in chat land in uploads/ — "
        "pass those paths here BEFORE emitting any workflow that loads an image, and wire the "
        "SERVER-SIDE names this returns (not the local paths) into each LoadImage node's "
        "`image` input. Several image roles (start frame, end frame, mask, reference)? Ask the "
        "user which file is which before wiring — filenames lie."
    )
    parameters = {
        "type": "object",
        "required": ["paths"],
        "properties": {
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Workspace paths to upload, e.g. ['uploads/a1b2-face.png']. "
                "Relative means workspace-relative.",
            },
            "subfolder": {
                "type": "string",
                "description": "Optional input subfolder on the instance. Omit for the root.",
            },
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            paths = [str(p).strip() for p in (params.get("paths") or []) if str(p).strip()]
            if not paths:
                return ToolResult.text("paths must name at least one file", is_error=True)
            subfolder = str(params.get("subfolder") or "").strip()

            # Relative means WORKSPACE-relative, resolved here so the trusted (in-process) and
            # sandboxed paths agree — the raw `fetch` would read a bare relative path against
            # the process CWD, which is nowhere near this run's uploads/.
            root = Path(current_workspace(".") or ".")

            uploaded: dict = {}
            failures: list[str] = []
            for path in paths:
                if abort.is_set():
                    break
                p = Path(path)
                # `overwrite` on purpose: iterating means re-sending a file under the same
                # name, and "input/foo (1).png" quietly diverging from what the workflow names
                # is exactly the kind of drift nobody can debug from here.
                form = {"overwrite": "true"}
                if subfolder:
                    form["subfolder"] = subfolder
                res = fetch(
                    "${COMFYUI_URL}/api/upload/image",
                    method="POST",
                    headers=_headers(),
                    file_path=str(p if p.is_absolute() else root / p),
                    file_field="image",
                    form_fields=form,
                    timeout_s=120.0,
                )
                if not res.ok:
                    failures.append(_failed(res, path))
                    continue
                try:
                    body = res.json()
                except ValueError:
                    failures.append(f"{path}: the instance did not return JSON")
                    continue
                name = str(body.get("name") or "")
                sub = str(body.get("subfolder") or "")
                # What LoadImage's enum actually lists: "subfolder/name" when there is one.
                uploaded[path] = f"{sub}/{name}" if sub else name

            lines = [f"{local}  ->  {server}" for local, server in uploaded.items()]
            if lines:
                lines.append(
                    "Use the RIGHT-hand names in LoadImage nodes. ComfyUI lists new files "
                    "immediately for /prompt; the browser's dropdown may need its Refresh."
                )
            lines += failures
            return ToolResult.text(
                "\n".join(lines) or "nothing uploaded",
                details={"uploaded": uploaded, "failed": len(failures)},
                is_error=not uploaded,
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"comfy_upload failed: {type(e).__name__}: {e}", is_error=True)


class ComfyDownloadTool(Tool):
    name = "comfy_download"
    label = "Download rendered outputs"
    default_retryable = True
    description = (
        "Pull rendered outputs (images, video) from the ComfyUI instance into this run's "
        "workspace and show them IN THE CHAT as artifacts. Pass the manifest entries comfy_run "
        "returned (filename/subfolder/type). Use it after a successful run so the user sees the "
        "result here instead of having to open their instance — then still ask whether it is "
        "right; you may not be able to see it yourself."
    )
    parameters = {
        "type": "object",
        "required": ["files"],
        "properties": {
            "files": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["filename"],
                    "properties": {
                        "filename": {"type": "string"},
                        "subfolder": {"type": "string"},
                        "type": {
                            "type": "string",
                            "description": "'output' (default) or 'temp' for PreviewImage results.",
                        },
                    },
                },
                "description": "Manifest entries from comfy_run, verbatim.",
            }
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            entries = [e for e in (params.get("files") or []) if isinstance(e, dict)]
            if not entries:
                return ToolResult.text("files must name at least one output", is_error=True)

            root = Path(current_workspace(".") or ".")
            saved: list[str] = []
            failures: list[str] = []
            for e in entries:
                if abort.is_set():
                    break
                filename = str(e.get("filename") or "").strip()
                if not filename:
                    failures.append("an entry without a filename — skipped")
                    continue
                query = {"filename": filename, "type": str(e.get("type") or "output")}
                sub = str(e.get("subfolder") or "").strip()
                if sub:
                    query["subfolder"] = sub
                # Basename only for the local file: the server's subfolder is ITS layout, and a
                # filename with separators must not steer where this run writes.
                dest = root / "outputs" / Path(filename).name
                res = fetch(
                    "${COMFYUI_URL}/api/view",
                    headers=_headers(),
                    params=query,
                    save_path=str(dest),
                    timeout_s=300.0,
                )
                if not res.ok:
                    failures.append(_failed(res, filename))
                    continue
                saved.append(str(dest))
                import studio_state

                studio_state.render_saved(str(dest))

            lines = [f"downloaded: {p}" for p in saved] + failures
            return ToolResult.text(
                "\n".join(lines) or "nothing downloaded",
                details={"saved": saved, "failed": len(failures)},
                artifacts=saved,  # what makes the images/videos render in the chat
                is_error=not saved,
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"comfy_download failed: {type(e).__name__}: {e}", is_error=True)


class ComfyRunTool(Tool):
    name = "comfy_run"
    label = "Run a ComfyUI workflow"
    default_retryable = False
    description = (
        "Submit an API-format workflow to the instance and wait for it. Returns what was "
        "produced and where. A REJECTED workflow comes back with the server's own node errors, "
        "which name the bad input and the values it would accept — repair from those rather "
        "than guessing."
    )
    parameters = {
        "type": "object",
        "required": ["workflow_path"],
        "properties": {
            "workflow_path": {
                "type": "string",
                "description": "Path to the API-format workflow JSON (what comfy_emit wrote).",
            },
            "timeout_s": {
                "type": "number",
                "description": "How long to wait for the run. Default 300.",
            },
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        import asyncio
        from pathlib import Path

        try:
            path = Path(str(params.get("workflow_path") or "").strip())
            if not path.is_file():
                return ToolResult.text(f"no workflow at {path}", is_error=True)
            try:
                prompt = json.loads(path.read_text(encoding="utf-8"))
            except ValueError as e:
                return ToolResult.text(f"{path} is not valid JSON: {e}", is_error=True)
            if isinstance(prompt, dict) and "nodes" in prompt:
                return ToolResult.text(
                    "that is a UI-format workflow, which POST /prompt does not accept. Use the "
                    "API-format file (comfy_emit writes one), or ask the user to export theirs "
                    "with 'Export (API)' — a hand conversion loses muted nodes and widget order.",
                    is_error=True,
                )

            res = _post("/api/prompt", {"prompt": prompt})
            if res.status == 400:
                try:
                    body = res.json()
                except ValueError:
                    body = {"error": res.text[:500]}
                return ToolResult.text(
                    "the instance REJECTED this workflow:\n"
                    + json.dumps(body, indent=2)[:3000]
                    + "\n\nEach node error names the input and, for a bad enum, the values this "
                    "instance accepts. Fix those and resubmit.",
                    details=body,
                    is_error=True,
                )
            if not res.ok:
                return ToolResult.text(_failed(res, "submit"), is_error=True)
            queued = res.json()
            prompt_id = str(queued.get("prompt_id") or "")
            if not prompt_id:
                return ToolResult.text(
                    f"the instance accepted the request but named no prompt_id: {res.text[:300]}",
                    is_error=True,
                )

            # Studio telemetry: the run is on the record from the moment it is queued. The
            # checkpoint and step count come from the graph itself — the first loader's
            # *_name and the first KSampler's steps, which is what the dashboard's history
            # table wants to say about a run.
            import studio_state

            ckpt, steps = "", None
            for node in prompt.values():
                inputs = node.get("inputs") or {} if isinstance(node, dict) else {}
                for field, value in inputs.items():
                    if not ckpt and field.endswith("_name") and isinstance(value, str) and (
                        value.endswith((".safetensors", ".ckpt", ".gguf", ".sft"))
                    ):
                        ckpt = value
                    if steps is None and field == "steps" and isinstance(value, (int, float)):
                        steps = int(value)
            studio_state.run_started(path.name, prompt_id, ckpt, steps)

            deadline = float(params.get("timeout_s") or 300.0)
            waited = 0.0
            step = 1.0
            while waited < deadline:
                if abort.is_set():
                    studio_state.run_finished(prompt_id, "interrupted")
                    return ToolResult.text(
                        f"stopped waiting for {prompt_id}; it may still be running on the "
                        f"instance.",
                        is_error=True,
                    )
                hist = _get(f"/api/history/{prompt_id}")
                entry = hist.json().get(prompt_id) if hist.ok and hist.text.strip() else None
                if entry:
                    status = (entry.get("status") or {}).get("status_str") or "unknown"
                    outputs = entry.get("outputs") or {}
                    files = []
                    for node_id, out in outputs.items():
                        if not isinstance(out, dict):
                            continue
                        # NOT just "images": video nodes write "gifs", audio writes "audio". Any
                        # list of dicts carrying a filename is an output.
                        for values in out.values():
                            if not isinstance(values, list):
                                continue
                            for item in values:
                                if isinstance(item, dict) and item.get("filename"):
                                    files.append({"node": node_id, **item})
                    if status != "success":
                        messages = (entry.get("status") or {}).get("messages") or []
                        studio_state.run_finished(prompt_id, "failed")
                        return ToolResult.text(
                            f"the run FAILED ({status}). What the instance reported:\n"
                            + json.dumps(messages, indent=2)[:2000],
                            details=entry,
                            is_error=True,
                        )
                    if files:
                        studio_state.run_finished(prompt_id, "complete", outputs=len(files))
                        lines = [f"ran successfully — {len(files)} output(s):"]
                        for f in files:
                            sub = f.get("subfolder") or ""
                            lines.append(
                                f"  node {f['node']}: {f['filename']}"
                                + (f" (in {sub})" if sub else "")
                                + f" [{f.get('type') or 'output'}]"
                            )
                        lines.append(
                            "Pass these entries to comfy_download to pull them into the chat, "
                            "then ask whether they are right — you cannot see the pixels."
                        )
                        return ToolResult.text("\n".join(lines), details=entry)
                    # A success whose outputs have not landed yet — a known race. Keep waiting
                    # rather than reporting an empty run as finished.
                studio_state.run_tick(prompt_id)
                await asyncio.sleep(step)
                waited += step
                step = min(step * 1.5, 5.0)
            return ToolResult.text(
                f"still running after {int(deadline)}s (prompt {prompt_id}). It has not failed — "
                f"check the instance, or call again with a longer timeout_s.",
                is_error=True,
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"comfy_run failed: {type(e).__name__}: {e}", is_error=True)


class ComfyStudioStateTool(Tool):
    name = "comfy_studio_state"
    label = "Studio telemetry"
    default_retryable = True
    description = (
        "Run telemetry for this agent's WINDOW (the Studio dashboard): instance facts, run "
        "history, active run, downloaded renders — returned as structured details. The window "
        "polls this; the model has no reason to call it (everything here was already reported "
        "in the turns that produced it)."
    )
    parameters = {"type": "object", "properties": {}}

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            import studio_state

            state = studio_state.read()
            return ToolResult.text(
                f"studio state: {len(state.get('runs') or [])} run(s), "
                f"{len(state.get('renders') or [])} render(s)",
                details=state,
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"comfy_studio_state failed: {type(e).__name__}: {e}", is_error=True)


class ComfyInterruptTool(Tool):
    name = "comfy_interrupt"
    label = "Interrupt the running job"
    default_retryable = False
    description = (
        "Stop whatever the ComfyUI instance is currently executing (POST /interrupt). Use when "
        "the user asks to stop a run, or from the window's Interrupt button. The interrupted "
        "run reports as failed/interrupted in its comfy_run result."
    )
    parameters = {"type": "object", "properties": {}}

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            res = _post("/api/interrupt", None, timeout_s=15.0)
            if not res.ok:
                return ToolResult.text(_failed(res, "interrupt"), is_error=True)
            return ToolResult.text("interrupt sent — the instance stops its current node.")
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"comfy_interrupt failed: {type(e).__name__}: {e}", is_error=True)


def register(api, ctx):
    # Imported by bare name: the loader puts this plugin's folder on sys.path, so siblings are
    # top-level modules here rather than a package.
    from comfy_emit import ComfyEmitTool
    from comfy_research import ComfyResearchTool

    api.register_tool(ComfyProbeTool())
    api.register_tool(ComfyEmitTool())
    api.register_tool(ComfyInventoryTool())
    api.register_tool(ComfyNodeSpecTool())
    api.register_tool(ComfyResearchTool())
    api.register_tool(ComfyUploadTool())
    api.register_tool(ComfyDownloadTool())
    api.register_tool(ComfyRunTool())
    api.register_tool(ComfyStudioStateTool())
    api.register_tool(ComfyInterruptTool())

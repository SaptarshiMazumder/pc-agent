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

import asyncio
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


# WHERE A CHAT-PASTED CONNECTION LIVES. When the settings page defeats someone, they can paste
# their instance URL straight into the conversation and `comfy_connect` records it here, in the
# run's own workspace. Every tool then uses it. Precedence: this file WINS when it exists,
# because it only exists when the user explicitly pasted a connection just now — that intent
# should beat a stale or empty setting. `comfy_connect` with no url clears it, handing control
# back to the settings.
_CONN_FILE = ".studio/connection.json"


def _override() -> dict | None:
    """The chat-pasted connection for this run, or None. {url, auth} — `url` already normalised
    by `comfy_connect` (token folded onto the query, path clean), `auth` a header value or ''."""
    try:
        raw = (Path(current_workspace(".") or ".") / _CONN_FILE).read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) and data.get("url") else None
    except (OSError, ValueError):
        return None


def _normalise_pasted_url(pasted: str) -> str:
    """A URL a user pasted -> a base the plugin can append `/api/...` to without breaking it.

    Same fold the host does for a `${SETTING}`, but here in the plugin because the value is a
    LITERAL it holds (not a placeholder the host resolves): split off a `?token=`/userinfo,
    keep origin + path, and remember the query to re-attach per request. Returns the origin+path
    with the query stripped; the query is recovered by `_split_query`.
    """
    from urllib.parse import urlsplit, urlunsplit

    p = urlsplit(pasted.strip())
    host = p.hostname or ""
    if p.port:
        host = f"{host}:{p.port}"
    if p.username:
        host = (p.username + (f":{p.password}" if p.password else "") + "@" + host)
    return urlunsplit((p.scheme, host, p.path.rstrip("/"), "", "")) + (
        f"#q={p.query}" if p.query else ""
    )


def _split_query(base: str) -> tuple[str, str]:
    """(origin+path, query) from a base `_normalise_pasted_url` produced — the query rides in a
    `#q=` fragment so it survives storage and is re-attached at request time."""
    if "#q=" in base:
        b, q = base.split("#q=", 1)
        return b, q
    return base, ""


def _headers() -> dict:
    """The credential headers. When a chat-pasted connection is active, its auth (if any) rides
    as a literal Authorization value; otherwise the PLACEHOLDER form the host substitutes from
    the per-account settings.

    The placeholders are written unconditionally in the settings case because this code cannot
    see whether a setting is filled in — and must not, that is the point. An unset one
    substitutes to nothing and the request goes out without it.
    """
    conn = _override()
    if conn is not None:
        auth = str(conn.get("auth") or "")
        return {"Authorization": auth} if auth else {}
    return {
        "Authorization": "${COMFYUI_AUTH}",
        "Modal-Key": "${COMFYUI_AUTH2}",
        "Modal-Secret": "${COMFYUI_AUTH3}",
    }


def _url(path: str) -> str:
    """The URL for one API path — from the chat-pasted override if present, else the
    `${COMFYUI_URL}` placeholder the host folds from settings."""
    conn = _override()
    if conn is not None:
        base, query = _split_query(str(conn["url"]))
        u = f"{base}{path}"
        return f"{u}?{query}" if query else u
    return f"${{COMFYUI_URL}}{path}"


def _get(path: str, timeout_s: float = 30.0):
    return fetch(_url(path), headers=_headers(), timeout_s=timeout_s)


def _post(path: str, body, timeout_s: float = 60.0):
    return fetch(_url(path), method="POST", json=body, headers=_headers(), timeout_s=timeout_s)


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
                    _url("/api/upload/image"),
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
                    _url("/api/view"),
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


#: ComfyUI-Manager's model folders, keyed by the model KIND the agent already reasons in.
#: Manager needs both a `save_path` (the folder under models/) and a `type` label; these are the
#: stock ones every template ships. An unknown kind falls back to save_path == kind, which is
#: what a custom folder would be called anyway.
_MANAGER_DIRS = {
    "checkpoint": ("checkpoints", "checkpoints"),
    "checkpoints": ("checkpoints", "checkpoints"),
    "unet": ("unet", "unet"),
    "diffusion_model": ("diffusion_models", "diffusion_models"),
    "diffusion_models": ("diffusion_models", "diffusion_models"),
    "vae": ("vae", "VAE"),
    "text_encoder": ("text_encoders", "text_encoders"),
    "text_encoders": ("text_encoders", "text_encoders"),
    "clip": ("clip", "clip"),
    "lora": ("loras", "loras"),
    "loras": ("loras", "loras"),
    "controlnet": ("controlnet", "controlnet"),
    "upscale": ("upscale_models", "upscale_models"),
    "upscale_models": ("upscale_models", "upscale_models"),
}


def _manager_present() -> bool:
    """Does this instance have ComfyUI-Manager? Its queue-status endpoint is the cheapest tell.
    Present on almost every rented-GPU template (vast, RunPod); absent on a bare install."""
    res = _get("/manager/queue/status", timeout_s=15.0)
    return res.ok


class ComfyInstallTool(Tool):
    name = "comfy_install"
    label = "Install a model on the instance"
    default_retryable = False
    description = (
        "Download a model onto the user's ComfyUI instance — WITHOUT asking them to touch a "
        "terminal — using ComfyUI-Manager, which most rented-GPU templates (vast, RunPod) ship. "
        "Give the filename, its download URL (comfy_research finds these on Hugging Face/"
        "Civitai), and its kind (checkpoint, unet, vae, text_encoder, lora, controlnet, "
        "upscale…). It queues the download, waits for it, and confirms the file is loadable. "
        "This is how you FIX a missing-model workflow yourself instead of handing the user a "
        "list. If the instance has no Manager, it says so and names the fallback."
    )
    parameters = {
        "type": "object",
        "required": ["filename", "url", "kind"],
        "properties": {
            "filename": {
                "type": "string",
                "description": "The exact filename to save as, e.g. 'wan2.2_vae.safetensors'.",
            },
            "url": {
                "type": "string",
                "description": "Direct download URL (a Hugging Face /resolve/ link, a Civitai "
                "download URL). comfy_research surfaces these.",
            },
            "kind": {
                "type": "string",
                "description": "Where it belongs: checkpoint | unet | diffusion_model | vae | "
                "text_encoder | clip | lora | controlnet | upscale.",
            },
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            filename = str(params.get("filename") or "").strip()
            url = str(params.get("url") or "").strip()
            kind = str(params.get("kind") or "").strip().lower()
            if not (filename and url and kind):
                return ToolResult.text("filename, url and kind are all required", is_error=True)
            if not _manager_present():
                return ToolResult.text(
                    "this instance has no ComfyUI-Manager, so I cannot install models over its "
                    "API. Two ways forward: (1) install ComfyUI-Manager on the instance (most "
                    "rented-GPU templates already have it — check yours), or (2) set this agent's "
                    "COMFYUI_MCP_URL to an instance MCP that exposes model installing. Failing "
                    "both, the file has to be downloaded on the instance itself.",
                    is_error=True,
                )

            save_path, mtype = _MANAGER_DIRS.get(kind, (kind, kind))
            body = {
                "ui_id": f"agent-{filename}",
                "filename": filename,
                "url": url,
                "save_path": save_path,
                "type": mtype,
                "base": "",
            }
            res = _post("/manager/queue/install_model", body, timeout_s=30.0)
            if not res.ok:
                return ToolResult.text(_failed(res, f"install {filename}"), is_error=True)
            # Manager queues the job; the worker has to be told to run.
            _post("/manager/queue/start", None, timeout_s=15.0)

            # Poll to completion. A 14B fp16 weight is minutes of download, so the ceiling is
            # generous and the message says "still going" rather than "failed" on a timeout.
            waited, step, deadline = 0.0, 3.0, 1800.0
            while waited < deadline:
                if abort.is_set():
                    return ToolResult.text(
                        f"stopped waiting for {filename}; Manager is still downloading it on the "
                        "instance. Call comfy_inventory shortly to see if it landed.",
                        is_error=True,
                    )
                st = _get("/manager/queue/status", timeout_s=15.0)
                try:
                    info = st.json() if st.ok and st.text.strip() else {}
                except ValueError:
                    info = {}
                if info and not info.get("is_processing") and int(info.get("in_progress_count") or 0) == 0:
                    break
                await asyncio.sleep(step)
                waited += step
                step = min(step * 1.3, 15.0)

            # Confirm it is actually loadable now — a finished queue with the file still invisible
            # means it landed somewhere a loader does not look (wrong kind), which is worth saying.
            inv = _get("/api/object_info", timeout_s=60.0)
            visible = False
            try:
                if inv.ok:
                    visible = any(
                        filename in files
                        for _c, _i, files in _model_enums(inv.json())
                    )
            except ValueError:
                pass
            if visible:
                return ToolResult.text(
                    f"installed {filename} into models/{save_path}/ — it is now loadable. "
                    "Design with it."
                )
            return ToolResult.text(
                f"the download for {filename} finished, but no loader lists it yet. ComfyUI only "
                "rescans its model folders on restart or a Manager refresh — try comfy_inventory "
                f"again in a moment. If it still does not appear, the kind may be wrong: I put it "
                f"in models/{save_path}/.",
                is_error=True,
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"comfy_install failed: {type(e).__name__}: {e}", is_error=True)


class ComfyConnectTool(Tool):
    name = "comfy_connect"
    label = "Connect from a pasted URL"
    default_retryable = True
    description = (
        "Use a ComfyUI instance the user PASTED INTO THE CHAT, when the settings page didn't "
        "work for them. Give it the URL they pasted (vast/RunPod give one with a ?token=… in "
        "it — pass it whole); this validates it by probing, and if it answers, every comfy tool "
        "uses it for this workspace until changed. Call with an empty url to clear it and go "
        "back to the saved settings. NOTE: a URL pasted in chat is visible to you and saved in "
        "the workspace, unlike a setting — for a long-lived secret, the settings page is better; "
        "this is the quick path when someone can't find it."
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The full instance URL the user pasted (token and all). Empty to "
                "clear the pasted connection and use settings instead.",
            },
            "auth": {
                "type": "string",
                "description": "Only if the instance needs a HEADER instead of a URL token — the "
                "whole value, e.g. 'Bearer …'. Usually leave empty; vast/RunPod carry the token "
                "in the URL.",
            },
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            conn_path = Path(current_workspace(".") or ".") / _CONN_FILE
            pasted = str(params.get("url") or "").strip()
            if not pasted:
                conn_path.unlink(missing_ok=True)
                return ToolResult.text(
                    "cleared the pasted connection — comfy tools will use the saved settings now."
                )
            if "//" not in pasted or not pasted.lower().startswith(("http://", "https://")):
                return ToolResult.text(
                    f"that does not look like a URL: {pasted!r}. It should start with http:// or "
                    "https:// — paste the address your provider gave you.",
                    is_error=True,
                )
            normalised = _normalise_pasted_url(pasted)
            auth = str(params.get("auth") or "").strip()

            # Validate before saving: probe the pasted instance directly, so a bad paste never
            # silently shadows a working setting. Write ONLY on a real answer.
            base, query = _split_query(normalised)
            probe_url = f"{base}/api/system_stats" + (f"?{query}" if query else "")
            res = fetch(
                probe_url, headers={"Authorization": auth} if auth else None, timeout_s=30.0
            )
            if not res.ok:
                return ToolResult.text(
                    _failed(res, "connect")
                    + "\nThe pasted connection was NOT saved. Check the URL is exactly what your "
                    "provider shows, and that the instance is running.",
                    is_error=True,
                )

            conn_path.parent.mkdir(parents=True, exist_ok=True)
            conn_path.write_text(
                json.dumps({"url": normalised, "auth": auth}), encoding="utf-8"
            )
            data = res.json() if res.text.strip() else {}
            system = data.get("system") or {}
            devices = data.get("devices") or []
            lines = [
                "connected using the URL you pasted — I'll use it for this session.",
                f"ComfyUI {system.get('comfyui_version') or 'unknown'}",
            ]
            for d in devices:
                free = int(d.get("vram_free") or 0) // (1024**3)
                total = int(d.get("vram_total") or 0) // (1024**3)
                lines.append(f"{d.get('name') or d.get('type')}: {free} GB free of {total} GB")
            import studio_state

            first = devices[0] if devices else {}
            studio_state.set_instance(
                version=system.get("comfyui_version"),
                gpu=first.get("name") or first.get("type"),
                vram_free=first.get("vram_free"),
                vram_total=first.get("vram_total"),
            )
            return ToolResult.text("\n".join(lines), details=data)
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"comfy_connect failed: {type(e).__name__}: {e}", is_error=True)


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

    api.register_tool(ComfyConnectTool())
    api.register_tool(ComfyInstallTool())
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

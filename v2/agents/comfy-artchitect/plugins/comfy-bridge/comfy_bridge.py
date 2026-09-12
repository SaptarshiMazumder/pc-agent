"""ComfyUI bridge — the agent's only route to a running instance.

EVERY REQUEST GOES THROUGH THE HOST. `fetch` is the brokered call: this module never opens a
socket, never reads an environment variable, never spawns anything.

THE INSTANCE IS PROVISIONED, AND ONLY EVER PROVISIONED. `gpu_ensure` (plugins/vast-bridge) asks
the platform for this user's machine and writes its address to `.studio/connection.json`; every
tool here reads that file and nothing else. There is no setting, no pasted URL and no manual
override — the pasted-URL path (`comfy_connect`) was removed because the sandbox no longer
fences hosts, so it was the one way this agent could be pointed at a box the platform did not
rent and does not control. One copy of this agent serves everyone because the FILE is per
workspace, not because anything in here is per caller.
(`comfy_research`, in its own module, uses the same brokered `fetch` to reach Hugging Face and
Civitai.)

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
import os
import re
from pathlib import Path
from urllib.parse import urlencode

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


# THE HANDOVER FILE. `gpu_ensure` writes {url, auth} here the moment the rented machine answers,
# and every tool in this module reads it. It is the ONLY source of the instance's address: no
# setting, no environment variable, no URL a user pasted. `gpu_release` deletes it, so a tool
# called after the machine is gone reports "no GPU is running" rather than dialling a corpse.
# Same constant as vast_bridge._CONN_FILE, duplicated rather than imported so neither plugin
# depends on the other's load order; the contract is the path and the {url, auth} shape.
_CONN_FILE = ".studio/connection.json"


def _override() -> dict | None:
    """The provisioned connection for this run, or None. {url, auth}: `url` as the platform wrote
    it, `auth` a header value or '' (a rented box carries none today)."""
    try:
        raw = (Path(current_workspace(".") or ".") / _CONN_FILE).read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) and data.get("url") else None
    except (OSError, ValueError):
        return None


def _split_query(base: str) -> tuple[str, str]:
    """(origin+path, query) from a stored base. A provider that hands out `?token=…` URLs has
    the query folded into a `#q=` fragment so it survives storage and is re-attached at request
    time; a plain `http://ip:port` — what the platform writes today — passes through untouched.
    Kept for the day a rented box needs a token, not for anything a user types."""
    if "#q=" in base:
        b, q = base.split("#q=", 1)
        return b, q
    return base, ""


def _headers() -> dict:
    """The credential headers: whatever `auth` the handover file carries, or nothing. A rented
    box has no auth today, so this is empty in practice; the seam stays so a provider that
    fronts the instance with a header can be adopted in `gpu_ensure` without touching a tool."""
    conn = _override()
    auth = str((conn or {}).get("auth") or "")
    return {"Authorization": auth} if auth else {}


def _url(path: str) -> str:
    """The URL for one API path, from the connection file. "" when there is no instance.

    THERE IS NO FALLBACK. The address is provisioned — `gpu_ensure` rents the machine and writes
    .studio/connection.json — and that file is the only place it comes from. A `${COMFYUI_URL}`
    placeholder here would resolve to nothing and go out as a request to a garbage address,
    reported as "could not reach the instance" — which reads as a broken GPU rather than as "no
    GPU has been started yet".
    """
    conn = _override()
    if conn is None:
        return ""
    base, query = _split_query(str(conn["url"]))
    u = f"{base}{path}"
    return f"{u}?{query}" if query else u


class _NoInstance:
    """A failed response for "there is no instance yet", shaped like a real one.

    Returned rather than raised so every caller's existing `if not res.ok` handling reports it
    the same way it reports any other failure — one error path, not two.
    """

    ok = False
    status = 0
    text = ""
    error = (
        "no GPU is running for this user yet. Call gpu_ensure first — it starts one (or reuses "
        "the one this user already has) and points every comfy tool at it. Do NOT ask the user "
        "for a URL."
    )

    def json(self):
        return {}


def _no_instance() -> "ToolResult":
    """The same guidance _NoInstance carries, as a tool result.

    UPLOAD AND DOWNLOAD BUILD THEIR OWN FETCH CALLS — one posts a multipart file, the other
    appends a query — so neither goes through `_get`/`_post` and neither got the empty-URL guard.
    They passed "" straight to the broker, which refused with "a fetch request needs a url": true,
    unactionable, and nothing to do with the real problem, which is that no GPU is running.
    """
    return ToolResult.text(_NoInstance.error, is_error=True)


def _get(path: str, timeout_s: float = 30.0):
    url = _url(path)
    return fetch(url, headers=_headers(), timeout_s=timeout_s) if url else _NoInstance()


def _post(path: str, body, timeout_s: float = 60.0):
    url = _url(path)
    if not url:
        return _NoInstance()
    return fetch(url, method="POST", json=body, headers=_headers(), timeout_s=timeout_s)


def _failed(res, what: str) -> str:
    """One sentence naming what went wrong, in the server's own words where there are any.

    A transport failure and a 401 need different fixes, and "could not reach ComfyUI" hides
    which one happened.
    """
    if res.error:
        return (
            f"{what}: could not reach the instance ({res.error}). If a GPU was running it may "
            f"have been reclaimed for being idle — call gpu_ensure to start one again."
        )
    if res.status in (401, 403):
        return (
            f"{what}: the instance refused the credential (HTTP {res.status}). A provisioned "
            f"GPU should never do this — call gpu_ensure to get a fresh one."
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


#: What comfy_inventory says when it is called before there is a design to check against.
#: Phrased as a redirection rather than a refusal, because the agent's next move matters more
#: than the error: it should go and research, not go and find another way to list files.
_INVENTORY_TOO_EARLY = (
    "comfy_inventory is not available yet — no workflow has been designed in this conversation.\n"
    "This is deliberate. What is already installed is NOT a design input: this instance is "
    "provisioned for this job and anything missing can be downloaded, so choosing from what "
    "happens to be lying around produces a worse workflow than the one the research supports.\n"
    "Do this instead: research the best model for what the user asked for (comfy_research, "
    "web_search), design the graph, and comfy_emit it. Inventory unlocks then — and that is when "
    "it is actually useful, for confirming a download landed."
)


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
        import studio_state

        # THE GATE IS FOR THE AGENT'S REASONING, NOT THE WINDOW'S PANEL. A `tools.invoke` from
        # the app is a person looking at a dashboard, not a model choosing what to build around,
        # and it runs under a different session key — so the emit marker would never be set and
        # the instance panel's model list would be refused forever.
        from agent_runtime.application.run_context import current_run_context

        if not getattr(current_run_context(), "direct_invoke", False):
            if not studio_state.has_emitted():
                return ToolResult.text(_INVENTORY_TOO_EARLY, is_error=True)
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

            downloading = _downloads_in_flight()
            if not found:
                return ToolResult.text(
                    ("nothing matching " + repr(needle) if needle else "no model files")
                    + " — no loader on this instance lists any. If models were just added, "
                    "ComfyUI only rescans its folders on restart or via its Refresh button."
                    + (f"\n{downloading}" if downloading else ""),
                    details={},
                )
            lines = [f"{len(found)} loader input(s) with model files:"]
            for key in sorted(found):
                items = found[key]
                shown = ", ".join(items[:10])
                more = f" … and {len(items) - 10} more" if len(items) > 10 else ""
                lines.append(f"{key} ({len(items)}): {shown}{more}")
            if downloading:
                lines.append(downloading)
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
        "a LoadImage node can use them. THE USER'S IMAGES ARE IN TWO PLACES: `references/` "
        "holds what they added as reference media (real filenames, e.g. "
        "references/influencer-reference.png), and `uploads/` holds what they attached in chat "
        "(uuid-prefixed, e.g. uploads/a1b2-face.png). Look in BOTH. Upload BEFORE emitting any "
        "workflow that loads an image, and wire the SERVER-SIDE names this returns — never the "
        "local paths — into each LoadImage node's `image` input. "
        "SEVERAL IMAGES MEANS SEVERAL ROLES (start frame, end frame, mask, identity "
        "reference). Work out which is which from the user's own words and the filenames, "
        "upload them all in one call, and SAY THE MAPPING in your plan — 'face.png is the "
        "identity reference, bg.png is the background' — so they can correct it in one line. Do "
        "not stop and ask; a stated mapping they can fix beats a question they have to answer "
        "before anything is built."
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
        if _override() is None:
            return _no_instance()
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
        if _override() is None:
            return _no_instance()
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
                # RELATIVE, NOT ABSOLUTE — and on the hosted backend that is the difference
                # between a render arriving and a 401-shaped refusal.
                #
                # `current_workspace()` answers in the SANDBOX's coordinates: on a microVM the
                # executor remaps it to that box's unpack dir (/tmp/exec-<id>/ws). But the fetch is
                # BROKERED — the host performs the download and writes the file — and the broker
                # validates save_path against the HOST workspace (fetch_broker._writable). So an
                # absolute path built here is guest-side, lands outside every host root, and is
                # refused with "outside this run's writable space" even though it is literally the
                # workspace. Subprocess sandboxes hide this: both sides are one filesystem.
                #
                # A relative path has no such ambiguity — the broker anchors it to the real
                # workspace itself (`p = Path(roots[0]) / p`), which is correct on both backends.
                # WORKSPACE-RELATIVE, AND REPORTED THAT WAY TOO. Writing it correctly is only
                # half the job: whatever path this tool NAMES is the path `read`, `show_files` and
                # the chat's artifact link will each try to use. An absolute path answers in the
                # sandbox's coordinates — on a microVM, /tmp/exec-<id>/ws — so the file downloads
                # fine and then every consumer of the name is refused, which reads as the download
                # having failed when it did not. A relative path means the same file to the
                # sandbox, to the fs tools, and to the window.
                rel = f"outputs/{Path(filename).name}"
                # THE QUERY GOES IN THE PATH, NOT IN `params`. This was the one call in the plugin
                # that used httpx's `params=`, and it was the one call that 401'd on any instance
                # whose URL carries a token. The host folds `${COMFYUI_URL}`'s own query — the
                # `?token=…` vast and RunPod hand out — into the resolved URL; httpx then REPLACES
                # that query with `params`, so the token was stripped a layer below where anyone
                # was looking. Every other call here builds its query into the path and works,
                # which is exactly why downloads were the only thing failing.
                # `_url` ALREADY carries a query when the user pasted a tokened URL in chat, so
                # the separator has to be chosen, not assumed — "?a=1?b=2" is not a URL.
                view = _url("/api/view")
                view += ("&" if "?" in view else "?") + urlencode(query)
                res = fetch(
                    view,
                    headers=_headers(),
                    save_path=rel,
                    timeout_s=300.0,
                )
                if not res.ok:
                    failures.append(_failed(res, filename))
                    continue
                saved.append(rel)
                import studio_state

                studio_state.render_saved(rel)

            lines = [f"downloaded: {p}" for p in saved] + failures
            return ToolResult.text(
                "\n".join(lines) or "nothing downloaded",
                details={"saved": saved, "failed": len(failures)},
                artifacts=saved,  # what makes the images/videos render in the chat
                is_error=not saved,
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"comfy_download failed: {type(e).__name__}: {e}", is_error=True)


async def _poll_run(prompt_id: str, deadline: float, abort) -> ToolResult | None:
    """Poll one queued run within `deadline` seconds. Returns the FINAL ToolResult (success or
    the instance's failure report), or None while the run is still going — the sandbox stops any
    tool at 120s, so the callers keep their wait window under it and hand a still-running run to
    comfy_run_status instead of dying mid-wait."""
    import studio_state

    waited, step = 0.0, 1.0
    while waited < deadline:
        if abort.is_set():
            return None
        hist = _get(f"/api/history/{prompt_id}")
        try:
            entry = hist.json().get(prompt_id) if hist.ok and hist.text.strip() else None
        except ValueError:
            entry = None
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
    return None


def _still_rendering(prompt_id: str) -> ToolResult:
    """The NON-error handoff for a run that outlives a tool's wait window."""
    return ToolResult.text(
        f"still rendering (prompt {prompt_id}) — a video render takes minutes and continues on "
        "the instance after this returns. Do other work if any is left, then call "
        f"comfy_run_status with prompt_id '{prompt_id}' to collect the outputs."
    )


#: The in-tool wait ceiling. The sandbox kills any tool at 120s; finishing under it with a
#: clean handoff beats dying mid-wait and reading as a phantom failure.
_RUN_WAIT_CAP_S = 100.0


# ─────────────────────────────── paid partner nodes ───────────────────────────────────────────
#
# ComfyUI ships official nodes for ~20 hosted providers (Kling, Veo, Runway, Luma, Flux, Recraft,
# Sora …). They bill THE PUBLISHER's prepaid Comfy balance, not the user's, and they authenticate
# with ONE account key rather than per-provider keys — which is what makes "the user supplies
# nothing" possible at all.
#
# THE KEY RIDES IN `extra_data`, NOT IN THE GRAPH. ComfyUI reads `extra_data.api_key_comfy_org`
# and passes it to nodes as a hidden input; it is never written into the workflow JSON or into
# history. So a workflow file on disk — or dragged into someone's browser — carries no credential,
# which is the same property `_fill_secrets` gives the ${…} placeholders above.
#
# WHY THE VALUE IS READ HERE rather than substituted by the host: the host substitutes `${…}` in
# URLs and HEADERS only, deliberately (a credential substituted into a BODY would land back in
# something the plugin can read). This one has to go in the body, so the plugin holds it.

#: The publisher's Comfy account key, AS A NAME. Declared in plugin.toml [sandbox] secrets and
#: substituted by the host into the outgoing body — this code never holds the value.
#:
#: It used to be read from os.environ here, which works in-process and returns "" inside the
#: sandbox: an installed agent's plugins are granted `secrets = {}` by design. The broker now
#: substitutes declared names in JSON bodies as it always has in headers, so the key can reach
#: `extra_data` without this plugin ever seeing it — and without the agent being made trusted.
_COMFY_KEY_REF = "${COMFY_API_KEY}"


def _quote_for(prompt: dict):
    """Price a graph, or None when pricing is unavailable.

    A BROKEN TABLE MUST NOT BILL. If the file is missing or malformed this returns None, and the
    caller refuses any graph containing partner nodes rather than running them unpriced.
    """
    try:
        import partner_pricing

        return partner_pricing.price_workflow(prompt)
    except Exception:  # noqa: BLE001
        return None


def _charge(credits: int, note: str) -> tuple[bool, str]:
    """Debit the caller's credits for a paid run. (ok, message).

    CHARGED AFTER A SUCCESSFUL SUBMIT, gated BEFORE it. `/debit` drains a partial balance rather
    than refusing, which is right for cheap model calls — the call already ran, so refusing only
    leaves the balance untouched and the pre-call gate never engages. One video generation is
    ~140 credits, two orders of magnitude larger, so the gate is what does the real work here and
    this is only the settlement.
    """
    from agent_runtime.infrastructure import accounts

    account_id = accounts.account_id()
    if not account_id or credits <= 0:
        return True, ""
    base = (accounts.api_base() or "").rstrip("/")
    if not base:
        return True, ""
    res = fetch(
        f"{base}/debit",
        method="POST",
        json={"account_id": account_id, "credits": int(credits), "agent_id": "comfy-artchitect"},
        headers={"X-Internal-Key": "${AGENTD_ACCOUNTS_INTERNAL_KEY}"},
        timeout_s=30.0,
    )
    if res.ok:
        return True, ""
    return False, f"could not charge {credits} credits for {note}: {res.error or res.status}"


def _affordable(credits: int) -> tuple[bool, str]:
    """Does the caller have the credits this run will cost? (ok, why not).

    THE GATE THAT ACTUALLY BITES. Fails OPEN when the balance cannot be read — an accounts blip
    must not block a user who has paid, and the settlement below still records the spend.
    """
    from agent_runtime.infrastructure import accounts

    account_id = accounts.account_id()
    base = (accounts.api_base() or "").rstrip("/")
    if not account_id or not base or credits <= 0:
        return True, ""
    res = fetch(
        f"{base}/budget/{account_id}",
        headers={"X-Internal-Key": "${AGENTD_ACCOUNTS_INTERNAL_KEY}"},
        timeout_s=20.0,
    )
    if not res.ok:
        return True, ""
    try:
        have = int((res.json() or {}).get("credits_remaining") or 0)
    except (ValueError, TypeError):
        return True, ""
    if have >= credits:
        return True, ""
    return False, (
        f"this run needs {credits} credits and the account has {have}. Offer the user a "
        "free/local alternative, or a cheaper paid model — do NOT submit it anyway."
    )


class ComfyRunTool(Tool):
    name = "comfy_run"
    label = "Run a ComfyUI workflow"
    default_retryable = False
    description = (
        "Submit an API-format workflow to the instance. A quick run returns its outputs "
        "directly; a long render (video) returns 'still rendering' with a prompt_id — collect "
        "it with comfy_run_status when done. A REJECTED workflow comes back with the server's "
        "own node errors, which name the bad input and the values it would accept — repair "
        "from those rather than guessing."
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
                "description": "How long to wait in-call before handing off to "
                "comfy_run_status. Default 75, capped at 100 (the sandbox stops longer waits).",
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

            # Secrets go in HERE, not when the workflow was written — see _fill_secrets.
            prompt, missing_keys = _fill_secrets(prompt)
            if missing_keys:
                return ToolResult.text(
                    "this workflow asks for API key(s) this platform does not hold: "
                    + ", ".join(missing_keys)
                    + ".\nDO NOT ask the user for them. Every paid model available here is a "
                    "ComfyUI partner node, authenticated by the platform — rebuild this part of "
                    "the graph with one (comfy_price lists them), or use an open-weight model. "
                    "If neither works, say plainly that the service is not available here.",
                    is_error=True,
                )
            # PAID PARTNER NODES: price, gate, then submit with the platform's key.
            quote = _quote_for(prompt)
            body: dict = {"prompt": prompt}
            charge_credits = 0
            if quote is None:
                # Pricing is unavailable. Only a problem if the graph actually uses paid nodes —
                # and we cannot tell which it is, so refuse only if the table is what failed.
                import partner_pricing  # re-raised here so the message names the real fault

                try:
                    partner_pricing.load_table()
                except Exception as e:  # noqa: BLE001
                    return ToolResult.text(
                        f"the partner-node price table could not be read ({e}). A workflow that "
                        "uses paid nodes cannot be priced, so it will not be submitted. Fix "
                        "partner-nodes.json.",
                        is_error=True,
                    )
            elif quote.paid or quote.unpriced:
                if not quote.ok:
                    return ToolResult.text(
                        "this workflow uses paid node(s) with no price on record, so it will not "
                        "be submitted:\n" + quote.as_text()
                        + "\nEither use a model that is priced, or have the operator add these "
                        "to partner-nodes.json.",
                        is_error=True,
                    )
                charge_credits = quote.credits
                affordable, why = _affordable(charge_credits)
                if not affordable:
                    return ToolResult.text(why, is_error=True)
                # THE KEY GOES IN extra_data AS A PLACEHOLDER — see the note above. If the
                # deployment holds no key the host leaves it literal and the partner node
                # answers 401 with the name visible, which is a debuggable failure rather than
                # a silent one.
                body["extra_data"] = {"api_key_comfy_org": _COMFY_KEY_REF}

            res = _post("/api/prompt", body)
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

            if charge_credits:
                # SETTLE ONLY ONCE THE INSTANCE HAS ACCEPTED IT. A graph rejected at submit never
                # reached a provider and never cost anything, so charging before this line would
                # bill for work that provably did not happen.
                charged, problem = _charge(charge_credits, "this run")
                if not charged:
                    # The job IS running and could not be billed. Surfaced rather than swallowed:
                    # silently unbilled paid runs are exactly how a prepaid balance drains with
                    # nobody noticing until the invoice.
                    print(f"comfy_run: BILLING FAILED - {problem}")

            deadline = min(float(params.get("timeout_s") or 75.0), _RUN_WAIT_CAP_S)
            result = await _poll_run(prompt_id, deadline, abort)
            return result if result is not None else _still_rendering(prompt_id)
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"comfy_run failed: {type(e).__name__}: {e}", is_error=True)


class ComfyRunStatusTool(Tool):
    name = "comfy_run_status"
    label = "Collect a running render"
    default_retryable = True
    description = (
        "Collect a run comfy_run handed off as 'still rendering'. Give it the prompt_id; it "
        "returns the outputs (pass them to comfy_download), the instance's failure report, or "
        "'still rendering' again — call it again after doing other work, a video render is "
        "minutes."
    )
    parameters = {
        "type": "object",
        "required": ["prompt_id"],
        "properties": {
            "prompt_id": {
                "type": "string",
                "description": "The prompt_id comfy_run returned.",
            },
            "timeout_s": {
                "type": "number",
                "description": "How long to wait in-call. Default 75, capped at 100.",
            },
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            prompt_id = str(params.get("prompt_id") or "").strip()
            if not prompt_id:
                return ToolResult.text("prompt_id is required", is_error=True)
            deadline = min(float(params.get("timeout_s") or 75.0), _RUN_WAIT_CAP_S)
            result = await _poll_run(prompt_id, deadline, abort)
            return result if result is not None else _still_rendering(prompt_id)
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(
                f"comfy_run_status failed: {type(e).__name__}: {e}", is_error=True
            )


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


#: `${NAME}` as written into an emitted workflow where a credential belongs.
_SECRET_REF = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _provider_keys() -> dict:
    """`NAME=value` pairs the DEPLOYMENT holds, as a dict. Normally empty.

    VESTIGIAL, AND DELIBERATELY KEPT. The user-facing BYOK setting behind this is gone: every
    paid model reachable here is a ComfyUI partner node the platform authenticates centrally, so
    nobody is asked for a key any more. What remains is the substitution machinery, so that an
    operator who exports a name into the daemon's environment can still make a third-party node
    pack work without a code change — and so a `${NAME}` left in a graph is reported as an
    unavailable service rather than posted to ComfyUI as a literal placeholder.

    One generic field rather than a named setting per provider: the list of paid services worth
    using changes faster than this file does, and a fixed set would be wrong within a month.
    SEPARATED BY NEWLINE **OR** SEMICOLON, and that is not cosmetic: the settings page renders a
    secret as a single-line `<input type="password">`, so a newline cannot be typed into it. A
    user pasting `A=1; B=2` on one line has to work, or the field is unusable for its own purpose.
    Blank entries and `#` comments are ignored so a user can label their own."""
    out: dict = {}
    raw = (os.environ.get("PROVIDER_KEYS") or "").replace(";", chr(10))
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name, value = name.strip(), value.strip()
        if name and value:
            out[name] = value
    return out


def _fill_secrets(graph: dict) -> tuple[dict, list]:
    """Replace `${NAME}` in every string input with its PROVIDER_KEYS value, AT SUBMIT TIME.

    THE POINT IS THAT THE FILE NEVER HOLDS THE KEY. A paid API node wants a credential as a node
    input, so writing the real value when the workflow is emitted would put it in a file that is
    listed in the rail, openable in the viewer, downloadable, and draggable onto another tab. The
    graph on disk carries the placeholder; only the copy POSTed to ComfyUI carries the secret.

    Returns the filled graph and the names that had no value — those are worth telling the user
    about by NAME (never by value), because the alternative is ComfyUI rejecting a literal
    "${KLING_API_KEY}" with an error that explains nothing.
    """
    keys = _provider_keys()
    missing: list = []

    def walk(v):
        if isinstance(v, str):
            for m in _SECRET_REF.findall(v):
                if m in keys:
                    v = v.replace("${" + m + "}", keys[m])
                elif m not in missing:
                    missing.append(m)
            return v
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        return v

    return walk(graph), missing


def _manager_catalog() -> list[dict]:
    """Manager's own model catalog, straight from the instance. Its install endpoint WHITELISTS
    against this list — an install request must match a catalog entry's save_path+base+filename
    exactly (manager_server.check_whitelist_for_model), so inventing those fields guarantees a
    400 on any default-security instance. The catalog also carries an `installed` flag."""
    res = _get("/externalmodel/getlist?mode=cache", timeout_s=60.0)
    if not res.ok:
        return []
    try:
        return (res.json() or {}).get("models") or []
    except ValueError:
        return []


def _downloads_in_flight() -> str:
    """One line when ComfyUI-Manager is still downloading, else ''. The guard against the worst
    misread this agent made in testing: a mid-download file looks 'missing' or 'corrupt', and
    designing around it produces a knowingly-wrong graph. Absence of a file proves nothing
    while this line is non-empty."""
    st = _get("/manager/queue/status", timeout_s=10.0)
    try:
        info = st.json() if st.ok and st.text.strip() else {}
    except ValueError:
        info = {}
    n = int(info.get("in_progress_count") or 0)
    if not n and not info.get("is_processing"):
        return ""
    done = info.get("done_count")
    total = info.get("total_count")
    return (
        f"NOTE: ComfyUI-Manager is STILL DOWNLOADING {n} file(s) ({done}/{total} done). A file "
        "missing above may simply not have finished — wait and re-check before concluding it "
        "failed, and NEVER redesign a workflow around a file that is still downloading."
    )


def _catalog_near_matches(catalog: list[dict], filename: str, limit: int = 4) -> list[str]:
    """Cataloged entries closest to a filename Manager refused — shared-token overlap, so the
    model can pick a legal alternative stack instead of retrying a doomed request."""
    want = {t for t in re.split(r"[^a-z0-9]+", filename.lower()) if t and t != "safetensors"}
    scored = []
    for m in catalog:
        have = {t for t in re.split(r"[^a-z0-9]+", str(m.get("filename", "")).lower()) if t}
        overlap = len(want & have)
        if overlap:
            scored.append((overlap, m))
    scored.sort(key=lambda p: -p[0])
    return [
        f"{m.get('name')} (filename={m.get('filename')}, {m.get('size')}, type={m.get('type')})"
        for _s, m in scored[:limit]
    ]


class ComfyInstallTool(Tool):
    name = "comfy_install"
    label = "Install a model on the instance"
    default_retryable = False
    description = (
        "Download a model onto the user's ComfyUI instance — WITHOUT asking them to touch a "
        "terminal — using ComfyUI-Manager, which most rented-GPU templates (vast, RunPod) ship. "
        "Give the filename, its download URL (comfy_research finds these on Hugging Face/"
        "Civitai), and its kind (checkpoint, unet, vae, text_encoder, lora, controlnet, "
        "upscale…). It queues the download; small files it confirms loadable on the spot, a "
        "multi-GB weight keeps downloading on the instance AFTER this returns — do other work, "
        "then confirm with comfy_inventory before running a workflow that needs the file. "
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

            # Manager whitelists installs against its own catalog (save_path+base+filename must
            # match an entry). So: cataloged file -> submit the entry VERBATIM, never our guess.
            catalog = _manager_catalog()
            entry = next(
                (m for m in catalog
                 if str(m.get("filename", "")).lower() == filename.lower()),
                None,
            )
            if entry is not None:
                if str(entry.get("installed")) == "True":
                    return ToolResult.text(
                        f"{filename} is already installed (models/{entry.get('save_path')}/). "
                        "Design with it."
                    )
                save_path = str(entry.get("save_path") or save_path)
                body = {
                    "ui_id": f"agent-{filename}",
                    "filename": entry.get("filename"),
                    "url": entry.get("url") or url,
                    "save_path": entry.get("save_path"),
                    "type": entry.get("type"),
                    "base": entry.get("base", ""),
                    "name": entry.get("name", ""),
                }
            else:
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
                if res.status == 400 and entry is None:
                    alts = _catalog_near_matches(catalog, filename)
                    hint = (
                        f"Closest cataloged models: {'; '.join(alts)}. Consider redesigning the "
                        "workflow around a cataloged stack and calling comfy_install with that "
                        "exact filename. "
                        if alts
                        else "No close cataloged alternative exists. "
                    )
                    return ToolResult.text(
                        f"install {filename}: this instance's ComfyUI-Manager only installs "
                        f"models from its own catalog at its current security level, and "
                        f"'{filename}' is not in that catalog. {hint}Otherwise the file must be "
                        "added on the instance itself, or Manager's security_level set to "
                        "'weak' in its config.",
                        is_error=True,
                    )
                return ToolResult.text(_failed(res, f"install {filename}"), is_error=True)
            # Manager queues the job; the worker has to be told to run.
            _post("/manager/queue/start", None, timeout_s=15.0)

            # Poll only BRIEFLY. The sandbox stops any tool at 120s, so waiting out a multi-GB
            # download here turns a healthy install into a phantom failure (and feeds the loop
            # guard). Small files finish inside the window; a big one gets a SUCCESS result
            # saying the download continues server-side — Manager keeps going without us.
            waited, step, deadline = 0.0, 3.0, 75.0
            still_downloading = False
            while waited < deadline:
                if abort.is_set():
                    still_downloading = True
                    break
                st = _get("/manager/queue/status", timeout_s=15.0)
                try:
                    info = st.json() if st.ok and st.text.strip() else {}
                except ValueError:
                    info = {}
                if info and not info.get("is_processing") and int(info.get("in_progress_count") or 0) == 0:
                    break
                await asyncio.sleep(step)
                waited += step
            else:
                still_downloading = True
            if still_downloading:
                size = str((entry or {}).get("size") or "").strip()
                return ToolResult.text(
                    f"queued {filename}{f' ({size})' if size else ''} — the download is running "
                    f"on the instance and continues after this returns. Do other work (design "
                    "the graph, install the next file), then confirm it landed with "
                    f"comfy_inventory before running a workflow that needs it. Target: "
                    f"models/{save_path}/."
                )
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


def _node_catalog() -> dict:
    """ComfyUI-Manager's custom-NODE-PACK catalog, keyed by pack id. The sibling of
    `_manager_catalog` (models): same Manager, different registry. Each entry carries `title`,
    `repository`, `version`/`cnr_latest` and a `state` of installed / not-installed / disabled."""
    res = _get("/customnode/getlist?mode=cache", timeout_s=90.0)
    if not res.ok:
        return {}
    try:
        return (res.json() or {}).get("node_packs") or {}
    except ValueError:
        return {}


def _resolve_pack(catalog: dict, query: str) -> tuple[str, dict] | None:
    """A pack id, a title, or a GitHub URL -> (id, entry). Exact wins over fuzzy, so a query that
    names a pack exactly never resolves to a lookalike."""
    q = query.strip()
    if q in catalog:
        return q, catalog[q]
    low = q.lower().rstrip("/")
    for pid, entry in catalog.items():
        if pid.lower() == low:
            return pid, entry
    # A GitHub URL — how a human names a pack, and what research returns.
    if "github.com" in low:
        for pid, entry in catalog.items():
            if str(entry.get("repository", "")).lower().rstrip("/").rstrip(".git") == low.rstrip(".git"):
                return pid, entry
    for pid, entry in catalog.items():
        if str(entry.get("title", "")).lower() == low:
            return pid, entry
    return None


def _pack_candidates(catalog: dict, query: str, limit: int = 6) -> list[str]:
    """Closest packs by shared word tokens — what to offer when a query names nothing exactly."""
    want = {t for t in re.split(r"[^a-z0-9]+", query.lower()) if len(t) > 2}
    scored = []
    for pid, entry in catalog.items():
        hay = f"{pid} {entry.get('title','')}".lower()
        have = {t for t in re.split(r"[^a-z0-9]+", hay) if t}
        overlap = len(want & have)
        if overlap:
            scored.append((overlap, pid, entry))
    scored.sort(key=lambda p: -p[0])
    return [
        f"{pid}  ({e.get('title')}, {e.get('state')})" for _s, pid, e in scored[:limit]
    ]


class ComfyNodeInstallTool(Tool):
    name = "comfy_node_install"
    label = "Install a custom node pack"
    default_retryable = False
    description = (
        "Install a ComfyUI CUSTOM NODE PACK on the user's instance — IPAdapter, PuLID, a LoRA "
        "trainer, video helpers, anything in ComfyUI-Manager's registry — WITHOUT asking the user "
        "to touch Manager themselves. Give the pack's registry id, its title, or its GitHub URL "
        "(research and `comfy_validate`'s missing-node report both give you these). It queues the "
        "install through ComfyUI-Manager and restarts ComfyUI so the new nodes load. This is how "
        "you fix a `missing_node_type` / unknown-node-class yourself. Node packs are code: say "
        "which one you are installing and why before you call this."
    )
    parameters = {
        "type": "object",
        "required": ["pack"],
        "properties": {
            "pack": {
                "type": "string",
                "description": "Registry id ('comfyui_ipadapter_plus'), exact title, or GitHub "
                "URL of the node pack to install.",
            },
            "restart": {
                "type": "boolean",
                "description": "Restart ComfyUI after installing so the nodes load. Default true "
                "— a pack that is installed but not loaded still fails a workflow.",
            },
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            query = str(params.get("pack") or "").strip()
            if not query:
                return ToolResult.text("pack is required", is_error=True)
            if not _manager_present():
                return ToolResult.text(
                    "this instance has no ComfyUI-Manager, so custom node packs cannot be "
                    "installed over its API. Either install ComfyUI-Manager on the instance, or "
                    "set COMFYUI_MCP_URL to an instance MCP that can install node packs.",
                    is_error=True,
                )

            catalog = _node_catalog()
            if not catalog:
                return ToolResult.text(
                    "could not read ComfyUI-Manager's node registry from this instance "
                    "(/customnode/getlist). Manager may be an old build or still starting.",
                    is_error=True,
                )
            found = _resolve_pack(catalog, query)
            if found is None:
                alts = _pack_candidates(catalog, query)
                return ToolResult.text(
                    f"no node pack matching {query!r} in this instance's Manager registry "
                    f"({len(catalog)} packs)."
                    + (f" Closest: {'; '.join(alts)}. Call again with an exact id." if alts else "")
                    + " A pack that is genuinely absent from the registry can only be installed "
                    "from a raw git URL, which Manager refuses on a remote instance.",
                    is_error=True,
                )
            pack_id, entry = found
            state = str(entry.get("state") or "")
            title = str(entry.get("title") or pack_id)
            if state == "installed":
                return ToolResult.text(
                    f"{title} ({pack_id}) is already installed on this instance. If its nodes "
                    "still do not resolve, ComfyUI may need a restart to load them."
                )

            body = {
                "ui_id": f"agent-{pack_id}",
                "id": pack_id,
                # Manager reads these three directly — a missing key is a 500, not a 400.
                "version": str(entry.get("version") or entry.get("cnr_latest") or "latest"),
                "selected_version": "latest",
                "channel": "default",
                "mode": "cache",
                "repository": str(entry.get("repository") or ""),
            }
            res = _post("/manager/queue/install", body, timeout_s=30.0)
            if not res.ok:
                if res.status in (403, 404):
                    return ToolResult.text(
                        f"install {pack_id}: ComfyUI-Manager refused it "
                        f"(HTTP {res.status}). Its security_level must allow node-pack installs "
                        "('middle' or lower) — that is a Manager setting on the instance, not "
                        "something I can change from here. Tell the user exactly that.",
                        is_error=True,
                    )
                return ToolResult.text(_failed(res, f"install {pack_id}"), is_error=True)
            _post("/manager/queue/start", None, timeout_s=15.0)

            # Same wait discipline as comfy_install: stay under the sandbox's stop, then hand off.
            waited, step, deadline = 0.0, 3.0, 60.0
            done = False
            while waited < deadline:
                if abort.is_set():
                    break
                st = _get("/manager/queue/status", timeout_s=15.0)
                try:
                    info = st.json() if st.ok and st.text.strip() else {}
                except ValueError:
                    info = {}
                if info and not info.get("is_processing") and int(info.get("in_progress_count") or 0) == 0:
                    done = True
                    break
                await asyncio.sleep(step)
                waited += step

            if not (params.get("restart", True)):
                return ToolResult.text(
                    f"queued {title} ({pack_id}). ComfyUI must RESTART before its nodes load — "
                    "call comfy_node_install again with restart, or ask the user to restart."
                )
            # A pack that is installed but not loaded is still a missing node. Rebooting is the
            # step that makes it real, and Manager owns it.
            _post("/manager/reboot", None, timeout_s=20.0)
            return ToolResult.text(
                f"installed {title} ({pack_id})"
                + ("" if done else " (still finishing)")
                + " and restarted ComfyUI so its nodes load. The instance takes ~30-60s to come "
                "back: call comfy_probe until it answers, then comfy_node_spec on the node class "
                "you need to confirm it is there before emitting a workflow that uses it."
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(
                f"comfy_node_install failed: {type(e).__name__}: {e}", is_error=True
            )


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


class ComfyValidateTool(Tool):
    name = "comfy_validate"
    label = "Compile-check a workflow"
    default_retryable = True
    description = (
        "Check an emitted API-format workflow against THIS instance before anything is "
        "installed or run: every node class must exist, every link must point at a node in the "
        "graph, and every model filename it names must be loadable. The result is pass, or an "
        "itemized report whose missing-file list IS the install shopping list — design first, "
        "validate, install exactly what this names, then run. Pass the `.api.json` path "
        "comfy_emit returned."
    )
    parameters = {
        "type": "object",
        "required": ["workflow_path"],
        "properties": {
            "workflow_path": {
                "type": "string",
                "description": "Path to the .api.json file comfy_emit wrote.",
            }
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            path = str(params.get("workflow_path") or "").strip()
            try:
                graph = json.loads(Path(path).read_text(encoding="utf-8"))
            except OSError as e:
                return ToolResult.text(f"cannot read {path}: {e}", is_error=True)
            except ValueError as e:
                return ToolResult.text(f"{path} is not valid JSON: {e}", is_error=True)
            if not isinstance(graph, dict) or not graph:
                return ToolResult.text(f"{path} is empty or not an object", is_error=True)
            if "nodes" in graph and "links" in graph:
                return ToolResult.text(
                    f"{path} is a UI-format workflow — validate the .api.json comfy_emit wrote "
                    "beside it (the API file is the one that runs).",
                    is_error=True,
                )

            res = _get("/api/object_info", timeout_s=60.0)
            if not res.ok:
                return ToolResult.text(_failed(res, "validate"), is_error=True)
            try:
                catalogue = res.json()
            except ValueError:
                return ToolResult.text(
                    "the instance's node catalogue is too large to fetch whole — validate "
                    "per-node with comfy_node_spec instead.",
                    is_error=True,
                )

            unknown_nodes: list[str] = []
            missing_files: list[str] = []
            bad_enums: list[str] = []
            bad_links: list[str] = []
            for nid, entry in graph.items():
                if not isinstance(entry, dict):
                    continue
                cls = str(entry.get("class_type") or "")
                spec = catalogue.get(cls)
                if not isinstance(spec, dict):
                    unknown_nodes.append(f"node {nid}: class '{cls}' does not exist here")
                    continue
                sections = spec.get("input") or {}
                specs = {
                    name: s
                    for section in ("required", "optional")
                    for name, s in (sections.get(section) or {}).items()
                }
                for field, value in (entry.get("inputs") or {}).items():
                    if isinstance(value, list) and len(value) == 2:
                        if str(value[0]) not in graph:
                            bad_links.append(
                                f"node {nid}.{field} links to node {value[0]}, not in this graph"
                            )
                        continue
                    s = specs.get(field)
                    choices = s[0] if isinstance(s, list) and s else None
                    if not isinstance(choices, list) or not isinstance(value, str):
                        continue
                    if value in choices:
                        continue
                    if _looks_like_model_list(choices) or (
                        not choices and value.lower().endswith(_MODEL_EXTS)
                    ):
                        missing_files.append(f"{value}  (for {cls}.{field}, node {nid})")
                    else:
                        legal = ", ".join(str(c) for c in choices[:8])
                        bad_enums.append(
                            f"node {nid}.{field}: '{value}' is not one of [{legal}…]"
                        )

            downloading = _downloads_in_flight()
            if not (unknown_nodes or missing_files or bad_enums or bad_links):
                return ToolResult.text(
                    f"compiles: all {len(graph)} node(s) exist on this instance, links resolve, "
                    "and every model file it names is loadable. Safe to comfy_run."
                    + (f"\n{downloading}" if downloading else "")
                )
            lines = ["the workflow does NOT compile against this instance:"]
            if unknown_nodes:
                lines.append(
                    "unknown node classes — USUALLY A WRONG NAME, not a missing pack. Look each "
                    "one up (comfy_node_spec / comfy_inventory) and re-emit with the real class. "
                    "If the class truly belongs to a pack this instance lacks, install it "
                    "yourself with comfy_node_install:"
                )
                lines += [f"  {x}" for x in unknown_nodes]
            if bad_links:
                lines.append("broken links:")
                lines += [f"  {x}" for x in bad_links]
            if bad_enums:
                lines.append("invalid values (fix the workflow):")
                lines += [f"  {x}" for x in bad_enums]
            if missing_files:
                lines.append("model files to install (this is the comfy_install shopping list):")
                lines += [f"  {x}" for x in missing_files]
            if downloading:
                lines.append(downloading)
            return ToolResult.text("\n".join(lines), is_error=True)
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"comfy_validate failed: {type(e).__name__}: {e}", is_error=True)


class ComfyPriceTool(Tool):
    name = "comfy_price"
    label = "What the paid models cost"
    default_retryable = True
    description = (
        "What ComfyUI's paid partner nodes cost, in credits. Call it BEFORE choosing between a "
        "paid and a free route, and before the approve block, so the user is shown real numbers "
        "instead of 'this costs money'. With no arguments it lists every paid provider and model "
        "available. Give it a workflow name to price that emitted graph exactly, including "
        "video duration and resolution. The user is charged these credits when the run is "
        "submitted; nobody has to supply an API key."
    )
    parameters = {
        "type": "object",
        "properties": {
            "workflow": {
                "type": "string",
                "description": (
                    "An emitted workflow to price exactly, e.g. 'workflows/talking-head.api.json'"
                    " or just its name. Omit to list the whole catalogue."
                ),
            }
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            import partner_pricing

            table = partner_pricing.load_table()
            name = str(params.get("workflow") or "").strip()
            if not name:
                return ToolResult.text(self._catalogue(table), details={"table": table})

            path = _workflow_path(name)
            if path is None or not path.exists():
                return ToolResult.text(
                    f"no workflow named {name!r} in this workspace — emit it first, or call "
                    "comfy_price with no arguments to see what the paid models cost.",
                    is_error=True,
                )
            graph = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(graph, dict) and "nodes" in graph:
                return ToolResult.text(
                    "that is the UI-format file; price the .api.json one.", is_error=True
                )
            quote = partner_pricing.price_workflow(graph, table)
            head = (
                f"{path.name}: {quote.credits} credits"
                if quote.paid and quote.ok
                else f"{path.name}:"
            )
            return ToolResult.text(
                head + "\n" + quote.as_text(),
                details={"credits": quote.credits, "ok": quote.ok, "paid": quote.paid},
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"comfy_price failed: {type(e).__name__}: {e}", is_error=True)

    @staticmethod
    def _catalogue(table: dict) -> str:
        """Every paid model, cheapest first WITHIN each provider.

        Sorted by price rather than listed in file order, because this is read while choosing —
        and an unsorted list quietly nudges toward whichever entry happens to be first.
        """
        markup = float(table.get("markup") or 1.0)
        lines = [
            "Paid providers available through ComfyUI's partner nodes. Prices are CREDITS "
            "charged to the user; no API key is needed from them.",
        ]
        for provider in table.get("providers") or []:
            unit = str(provider.get("unit") or "per_run")
            suffix = "/second of video" if unit == "per_second" else "/run"
            rows = []
            for model, rates in (provider.get("models") or {}).items():
                cheapest = min(float(v) for v in rates.values())
                rows.append((cheapest * markup, model, rates))
            rows.sort()
            lines.append(f"\n{provider.get('label')} ({unit.replace('per_', 'per ')}):")
            for price, model, rates in rows:
                tiers = [k for k in rates if k != "_default"]
                extra = f"  [{', '.join(tiers)}]" if tiers else ""
                lines.append(f"   {model}: {price:.0f} credits{suffix}{extra}")
        lines.append(
            "\nA 5-second Kling clip at 720p is about "
            f"{17.72 * 5 * markup:.0f} credits; one Flux Ultra image about "
            f"{12.66 * markup:.0f}."
        )
        return "\n".join(lines)


def _workflow_path(name: str):
    """An emitted workflow by name, however loosely the caller named it."""
    root = Path(current_workspace(".") or ".")
    candidate = Path(name)
    if candidate.is_absolute() or name.startswith("workflows/"):
        return root / candidate if not candidate.is_absolute() else candidate
    stem = name[:-9] if name.endswith(".api.json") else name.removesuffix(".json")
    for guess in (f"workflows/{stem}.api.json", f"workflows/{stem}.json", name):
        path = root / guess
        if path.exists():
            return path
    return root / f"workflows/{stem}.api.json"


def register(api, ctx):
    # Imported by bare name: the loader puts this plugin's folder on sys.path, so siblings are
    # top-level modules here rather than a package.
    from comfy_emit import ComfyEmitTool
    from comfy_research import ComfyResearchTool

    api.register_tool(ComfyInstallTool())
    api.register_tool(ComfyNodeInstallTool())
    api.register_tool(ComfyProbeTool())
    api.register_tool(ComfyEmitTool())
    api.register_tool(ComfyValidateTool())
    api.register_tool(ComfyInventoryTool())
    api.register_tool(ComfyNodeSpecTool())
    api.register_tool(ComfyResearchTool())
    api.register_tool(ComfyUploadTool())
    api.register_tool(ComfyDownloadTool())
    api.register_tool(ComfyPriceTool())
    api.register_tool(ComfyRunTool())
    api.register_tool(ComfyRunStatusTool())
    api.register_tool(ComfyStudioStateTool())
    api.register_tool(ComfyInterruptTool())

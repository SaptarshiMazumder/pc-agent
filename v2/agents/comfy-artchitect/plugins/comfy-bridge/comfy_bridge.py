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
import time
from pathlib import Path
from urllib.parse import urlencode

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import (
    current_account_id,
    current_run_context,
    current_workspace,
)
from agent_runtime.infrastructure.net.outbound import fetch

import chat_paths
import reference_slots
import studio_state
from gpu_model_download_client import GpuModelDownloadClient
from model_installation_service import ModelInstallationService
from workflow_link import WorkflowLink
from workflow_reference_repository import WorkflowReferenceRepository

#: What a model file looks like in a loader's enum. The DETECTION is generic on purpose — the
#: previous version of this tool was a hardcoded list of seven loaders, which made every model
#: family that loads differently (Flux and friends live in unet/ behind UNETLoader, not
#: CheckpointLoaderSimple) simply invisible: a Flux-only instance reported "no models
#: installed". Matching by what the VALUES look like means a loader from a custom pack
#: installed five minutes ago is found the same way the stock ones are.
_MODEL_EXTS = (".safetensors", ".sft", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".onnx")

#: HOW A WAITING TOOL WAITS. A model download is minutes of nothing to do; comfy_install and
#: comfy_node_install hold their call open until the work is done, and these are the cadences
#: of the hold. Module-level so a verifier can shrink them.
_POLL_S = 5.0  # first Manager-queue poll; backs off to _POLL_MAX_S
_POLL_MAX_S = 15.0
_LEASE_EVERY_S = 90.0  # renew the GPU's idle lease (_WORK_LEASE_S) well inside its window
_NOTICE_EVERY_S = 30.0  # a progress line to the window
_LOADABLE_GRACE_S = 60.0  # Manager's rescan lag between "queue empty" and "a loader lists it"
_REBOOT_WAIT_S = 240.0  # a restarted ComfyUI answering again
_STATUS_FAILURES_MAX = 6  # consecutive unreadable queue statuses before "lost Manager"
#: ONE ATTEMPT of a waiting tool. Under the hosted executor's 900 s cap (infra
#: `executor_timeout_seconds`, a Lambda) with room for its transfers. The engine's guard times
#: an attempt out at exactly this and — because the tool declares retry_on_timeout — starts
#: another, which re-enters here and WAITS for the files it already queued instead of queueing
#: them again (studio_state.queued_at). Four attempts is close to an hour of download on any
#: backend; a desktop subprocess has no cap of its own and is sliced the same way, so the two
#: paths behave identically.
_WAIT_ATTEMPT_S = 840.0
_WAIT_ATTEMPTS = 4
#: A file queued this recently and still absent is waited on, never re-queued.
_QUEUED_MEMORY_S = 3600.0


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
# setting, no environment variable, no URL a user pasted. It outlives the machine: the idle
# reaper reclaims the instance server-side and nothing here is told, so a tool called after
# that dials a dead address and reports "could not reach the instance — call gpu_ensure",
# which rents (or reuses) one and rewrites this file. That is the whole recovery path.
# Same constant as vast_bridge._CONN_FILE, duplicated rather than imported so neither plugin
# depends on the other's load order; the contract is the path and the {url, auth} shape.
_CONN_FILE = ".studio/connection.json"


# THIS CHAT'S REFERENCE MEDIA LIVES IN ITS OWN FOLDER: references/<chat-key>/ — named by
# chat_paths, which also names the chat's workflows/ and outputs/ twins and documents the contract
# with the window that writes and lists them. This is the ONLY place comfy_upload will read from.
# The workspace is the account's, shared by every conversation, and a flat references/ meant a
# new chat saw every file every earlier chat had added — plus uploads/, the chat pastes — and the
# agent, told to use "the reference", picked one from another conversation. The folder is the
# map from chat to media; the gate below is what makes it binding.


def _locate_reference(root: Path, chat_dir: Path, path: str) -> str | None:
    """The WORKSPACE-RELATIVE path of the file `path` names, IF it is one of this chat's
    references; None otherwise.

    Matched by BASENAME inside the chat folder, so `references/x.png`, `references/<chat>/x.png`
    and plain `x.png` all mean the same file — the model is told the full path, but the gate is
    "is this file in this chat's folder", not "did the model spell the folder right". An
    absolute path, or a name that is not in the folder, is refused: that is exactly the
    another-chat's-file and chat-paste case this exists to stop.

    RELATIVE ON PURPOSE, and this is the third time this lesson has been paid for in this file.
    The path goes to the host's fetch, and in the sandbox the plugin's idea of the workspace is
    /tmp/exec-<id>/ws — a guest path the host does not own, so an absolute one is refused as
    "outside this run's files". A relative path means the same file on both sides: the host
    resolves it against the run's workspace, and so does the in-process fetch.
    """
    p = Path(path)
    if p.is_absolute():
        return None
    try:
        base = root.resolve()
        chat = chat_dir.resolve()
    except OSError:
        return None
    cand = (base / p).resolve()
    if cand.is_file():
        try:
            cand.relative_to(chat)
            return cand.relative_to(base).as_posix()
        except ValueError:
            pass
    by_name = chat / p.name
    if by_name.is_file():
        return by_name.relative_to(base).as_posix()
    # A RENDER THIS CHAT DOWNLOADED is an input too — that is how a storyboard frame becomes
    # the video's start frame, or a still goes through a try-on. Recorded per conversation by
    # comfy_download; another chat's renders are as foreign as its references.
    try:
        import studio_state

        mine = studio_state.downloaded_in_session()
    except Exception:  # noqa: BLE001
        mine = set()
    if mine and cand.is_file():
        try:
            rel = cand.relative_to(base).as_posix()
        except ValueError:
            return None
        if rel in mine:
            return rel
    return None


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


#: The platform, by NAME — the host substitutes both, as vast-bridge does: the address, and the
#: credential (the internal key on a hosted daemon, the signed-in person's token on a desktop).
#: Leases, the credit check and the debit all go here — NOT through `accounts.api_base()`, which
#: is daemon-process state and reads as empty inside a sandbox: the debit that used it returned
#: "charged" without charging anyone, on every paid run, on the web.
_ACCOUNTS = "${AGENTD_ACCOUNTS_URL}"
_AUTH = {"Authorization": "Bearer ${AGENTD_PLATFORM_TOKEN}"}

#: The platform's credits per DOLLAR OF PROVIDER COST — its ledger's default, used only when
#: /pricing cannot be read. The number the plugin used to assume was 100 (Comfy's own credits),
#: which billed a $1.40 clip as 182 credits on a platform where it is ~233,000.
_DEFAULT_CREDITS_PER_USD = 166_667.0
_platform_rate_cache: list = []


def _platform_rate() -> float:
    """Credits per provider dollar, from the platform (GET /pricing). Read once per process — a
    sandboxed tool is one process per call — and never a reason to fail a call: a missing
    platform answers with the ledger's default, and the DEBIT is sent in dollars anyway, so the
    server converts with the real rate regardless of what was quoted."""
    if _platform_rate_cache:
        return _platform_rate_cache[0]
    rate = _DEFAULT_CREDITS_PER_USD
    try:
        res = fetch(f"{_ACCOUNTS}/pricing", timeout_s=10.0)
        if res.ok:
            value = float((res.json() or {}).get("credits_per_usd") or 0.0)
            if value > 0:
                rate = value
    except Exception:  # noqa: BLE001 — a quote must not fail on a pricing blip
        pass
    _platform_rate_cache.append(rate)
    return rate


def _platform_credits(usd: float) -> int:
    import math

    return int(math.ceil(max(0.0, float(usd)) * _platform_rate()))


def _lease(seconds: int) -> None:
    """Confirm ownership before GPU work. A cleanup claim makes this fail, not falsely
    acknowledge a lease on a machine already being destroyed. Existing GPU jobs remain
    protected by the reaper's activity probes if the platform is temporarily unreachable."""
    account_id = current_account_id()
    if not account_id:
        return
    response = fetch(
        f"{_ACCOUNTS}/vast/heartbeat",
        method="POST",
        json={"account_id": account_id, "lease_seconds": int(seconds)},
        headers=_AUTH,
        timeout_s=15.0,
    )
    if not response.ok or response.json().get("alive") is not True:
        raise RuntimeError("GPU keepalive was not confirmed; call gpu_ensure before submitting more work")


#: How long a render or a download may hold the machine without anyone talking to it. SHORT,
#: because it is the weakest of the three witnesses: the daemon heartbeats while the run
#: produces events, and the reaper asks the box itself whether a render or a download is in
#: flight. Each poll renews it, so a long job is covered for as long as something is watching
#: it — and a job nobody is watching is not something to keep paying for. The platform caps
#: it (max_lease_seconds) regardless.
_WORK_LEASE_S = 3 * 60


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
    # The same declared wait as comfy_install: an inventory taken while Manager is downloading
    # holds until the download lands (see _settle_downloads).
    default_timeout_sec = _WAIT_ATTEMPT_S
    default_retryable = True
    default_retry_on_timeout = True
    default_max_retries = _WAIT_ATTEMPTS
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

        direct = bool(getattr(current_run_context(), "direct_invoke", False))
        if not direct:
            if not studio_state.has_emitted():
                return ToolResult.text(_INVENTORY_TOO_EARLY, is_error=True)
        try:
            # THE MODEL WAITS HERE, NOT IN A LOOP OF ITS OWN. The window's dashboard read
            # (direct_invoke) is a snapshot and must not hang on a download.
            settled = "" if direct else await _settle_downloads(abort, on_update, "inventory")
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

            downloading = settled
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
                    f"'{node_class}' is not installed on this instance. Class names are guessed "
                    "wrong more often than packs are missing: comfy_node_search finds the real "
                    "name from a provider or model word (\"Seedance\", \"Nano Banana\", "
                    "\"Wan3\"). A missing custom pack is the other cause.",
                    is_error=True,
                )
            # WHAT IS INSTALLED IS NOT A DESIGN INPUT — the same rule as comfy_inventory, and
            # this was its side door: a loader's file enum lists whatever the rented image ships
            # (an SD 1.5 checkpoint), and a model that saw it built around it. Hidden until a
            # workflow exists; after that the enum is exactly what validate needs.
            import studio_state

            if not studio_state.has_emitted():
                _hide_installed_files(spec)
            return ToolResult.text(json.dumps(spec, indent=2)[:4000], details=spec)
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(
                f"comfy_node_spec failed: {type(e).__name__}: {e}", is_error=True
            )


#: Loader inputs whose enum is the instance's list of installed WEIGHTS. These are ComfyUI's own
#: input names for its loader nodes — the schema, not a guess about what is installed — which is
#: what lets validate tell "not downloaded yet" from "not a legal value" on an EMPTY box, where
#: the enum holds nothing that looks like a filename (see the shopping-list split in
#: ComfyValidateTool).
_MODEL_FIELDS = frozenset({
    "ckpt_name", "unet_name", "lora_name", "vae_name", "clip_name", "clip_name1", "clip_name2",
    "clip_name3", "control_net_name", "style_model_name", "upscale_model_name", "gligen_name",
})
#: Loader inputs whose enum is the instance's list of uploaded MEDIA — a file list too, but not
#: one comfy_install fetches: those arrive through the reference slots.
_MEDIA_FIELDS = frozenset({"image", "video", "audio"})
_FILE_FIELDS = _MODEL_FIELDS | _MEDIA_FIELDS


def _hide_installed_files(spec: dict) -> int:
    """Replace every installed-file enum in a node spec with a one-line note. Returns how many."""
    hidden = 0
    for section in ("required", "optional"):
        for field, s in ((spec.get("input") or {}).get(section) or {}).items():
            if not (isinstance(s, list) and s and isinstance(s[0], list)):
                continue
            if field in _FILE_FIELDS or _looks_like_model_list(s[0]):
                s[0] = [
                    f"({len(s[0])} installed file(s) hidden: not a design input — research picks "
                    "the model, comfy_install fetches it, comfy_validate lists what is missing)"
                ]
                hidden += 1
    return hidden


class ComfyNodeSearchTool(Tool):
    """The catalogue by NAME. `comfy_node_spec` needs an exact class, and the class names of
    partner nodes are not guessable (Seedance 2.5 is `ByteDance2FirstLastFrameNode`; Nano Banana
    Pro is `GeminiImage2Node`) — an agent that guessed got "not installed" three times and
    concluded the model was unavailable. This answers "what is here for <word>" in one call, with
    the two flags that decide whether a node may be used at all: `api_node` (paid, billed through
    the platform) and `deprecated` (a successor exists — use it instead).

    ALLOWED BEFORE A DESIGN EXISTS, unlike comfy_inventory: this is the node CATALOGUE, the same
    for every instance of this ComfyUI version, not the model files someone happened to leave on
    the box. Knowing that a Seedance node exists does not anchor a design; it is what makes the
    research actionable."""

    name = "comfy_node_search"
    label = "Find ComfyUI nodes by name"
    default_retryable = True
    description = (
        "Search the instance's node catalogue by a word — a provider, a model, a task — and get "
        "the exact class names with their category and the api_node/deprecated flags. Use it to "
        "find a partner node's real class (\"Seedance\", \"Nano Banana\", \"Wan3\", \"Kling\", "
        "\"lip sync\") before comfy_node_spec, and to avoid deprecated nodes. Works before any "
        "workflow exists."
    )
    parameters = {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {
                "type": "string",
                "description": "A word or two: provider, model, task. Case-insensitive; matches "
                               "class name, display name, category and module.",
            },
            "api_only": {
                "type": "boolean",
                "description": "Only partner (paid, api_node) nodes. Default false.",
            },
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            query = str(params.get("query") or "").strip().lower()
            if not query:
                return ToolResult.text("query is required", is_error=True)
            api_only = bool(params.get("api_only"))
            res = _get("/api/object_info", timeout_s=60.0)
            if not res.ok:
                return ToolResult.text(_failed(res, "node search"), is_error=True)
            body = res.json() or {}
            words = [w for w in query.replace("-", " ").split() if w]
            rows = []
            for cls, spec in body.items():
                if not isinstance(spec, dict):
                    continue
                if api_only and not spec.get("api_node"):
                    continue
                hay = " ".join(
                    str(spec.get(k) or "") for k in ("display_name", "category", "python_module")
                ).lower() + " " + cls.lower()
                if all(w in hay for w in words):
                    rows.append((
                        0 if spec.get("api_node") else 1,
                        1 if spec.get("deprecated") else 0,
                        cls,
                        str(spec.get("display_name") or ""),
                        str(spec.get("category") or ""),
                        bool(spec.get("api_node")),
                        bool(spec.get("deprecated")),
                    ))
            rows.sort()
            if not rows:
                return ToolResult.text(
                    f"no node matches '{query}' on this instance. Try a shorter word (a provider "
                    "or model family), or the pack is not installed — comfy_node_install."
                )
            lines = [f"{len(rows)} node(s) match '{query}' (partner nodes first; deprecated last):"]
            for _, _, cls, disp, cat, api, dep in rows[:30]:
                flags = " · ".join(f for f in ("PARTNER/paid" if api else "", "DEPRECATED — use its successor" if dep else "") if f)
                lines.append(f"  {cls}  —  {disp}  [{cat}]" + (f"  {flags}" if flags else ""))
            if len(rows) > 30:
                lines.append(f"  … {len(rows) - 30} more; narrow the query")
            lines.append("Then comfy_node_spec <class> for its inputs and price badge.")
            return ToolResult.text("\n".join(lines), details={"matches": [r[2] for r in rows[:30]]})
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"comfy_node_search failed: {type(e).__name__}: {e}", is_error=True)


def _push_reference(located: str, subfolder: str = "") -> tuple[str, str | None]:
    """Send one of this chat's files to the instance's input folder. `located` is
    WORKSPACE-RELATIVE (see _locate_reference): the host's fetch resolves it against the real
    workspace, which is the only form that survives the sandbox's guest/host split — an
    absolute guest path was refused as "outside this run's files" and read as a network error.
    Returns (the name LoadImage lists — "subfolder/name" when there is one, None), or ("", error).

    `overwrite` on purpose: iterating means re-sending a file under the same name, and
    "input/foo (1).png" quietly diverging from what the workflow names is exactly the kind of
    drift nobody can debug from here."""
    form = {"overwrite": "true"}
    if subfolder:
        form["subfolder"] = subfolder
    res = fetch(
        _url("/api/upload/image"),
        method="POST",
        headers=_headers(),
        file_path=located,
        file_field="image",
        form_fields=form,
        timeout_s=120.0,
    )
    if not res.ok:
        return "", _failed(res, located)
    try:
        body = res.json()
    except ValueError:
        return "", "the instance did not return JSON"
    name = str(body.get("name") or "")
    folder = str(body.get("subfolder") or "")
    return (f"{folder}/{name}" if folder else name), None


class ComfyReferenceAssignTool(Tool):
    name = "comfy_reference_assign"
    label = "Assign a reference to a role"
    default_retryable = False
    description = (
        "Give one of this chat's reference files a ROLE — the slot a workflow names with `@role` "
        "— by renaming it `<role>.<ext>` in the chat's references folder. Use it when the user "
        "says which image is which ('the second one is the shirt', 'use handbag-5 as the "
        "garment'): the References panel shows the file under that role at once and comfy_run "
        "picks it up. Only this chat's files; one file per role — assigning a role another file "
        "holds replaces it."
    )
    parameters = {
        "type": "object",
        "required": ["file", "role"],
        "properties": {
            "file": {
                "type": "string",
                "description": "The file's name as the References panel or the announce shows it, e.g. 'handbag-5-.jpg'.",
            },
            "role": {
                "type": "string",
                "description": "The role, e.g. 'garment' — lowercase letters, digits, '-' or '_'.",
            },
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        role = str(params.get("role") or "").strip().lstrip(reference_slots.TOKEN)
        try:
            ws = Path(current_workspace(".") or ".")
            rel = reference_slots.assign(ws, str(params.get("file") or ""), role)
        except (ValueError, FileNotFoundError) as e:
            return ToolResult.text(str(e), is_error=True)
        return ToolResult.text(
            f"{rel} now fills {reference_slots.TOKEN}{role}. comfy_run uploads it and wires it in."
        )


class ComfyUploadTool(Tool):
    name = "comfy_upload"
    label = "Upload images to ComfyUI"
    default_retryable = True
    description = (
        "Push this chat's reference media to the ComfyUI instance's input folder, so a "
        "LoadImage node can use it. THE ONLY FILES THIS WILL SEND ARE THE ONES THE USER ADDED "
        "TO THIS CONVERSATION in the References panel — the message that announced "
        "them names their exact paths (references/<chat>/name.png) — plus renders THIS "
        "conversation brought back with comfy_download (outputs/…), so a storyboard frame or a "
        "still can be the next workflow's input. Nothing else exists as far as this tool is "
        "concerned: not files another conversation added, not images pasted into the chat "
        "(uploads/). If nothing was added to this "
        "chat, this tool says so — the user adds it in the References panel; never "
        "substitute a file from anywhere else. Upload BEFORE emitting any workflow that loads "
        "an image, and wire the SERVER-SIDE names this returns — never the local paths — into "
        "each LoadImage node's `image` input. SEVERAL IMAGES MEANS SEVERAL ROLES (start frame, "
        "end frame, mask, identity reference): work out which is which from the user's words "
        "and the filenames, upload them all in one call, and SAY THE MAPPING in your plan so a "
        "wrong guess costs them one line to correct."
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

            # THE GATE. Only this chat's folder is readable here — see chat_paths.
            chat_dir = chat_paths.chat_dir(root, chat_paths.REFERENCES)
            available = (
                sorted(f.name for f in chat_dir.iterdir() if f.is_file())
                if chat_dir.is_dir()
                else []
            )
            if not available:
                return ToolResult.text(
                    "nothing has been added to THIS chat in the References panel, so there is "
                    "nothing to upload. Only media added to this conversation can go to the "
                    "instance — never a file from another chat, and never an image pasted into "
                    "the chat. The user adds it in the References panel (a slot, or Add), then "
                    "upload it.",
                    is_error=True,
                )

            uploaded: dict = {}
            failures: list[str] = []
            for path in paths:
                if abort.is_set():
                    break
                located = _locate_reference(root, chat_dir, path)
                if located is None:
                    failures.append(
                        f"{path}: not a file added to THIS chat, nor a render this chat "
                        "downloaded. This chat's reference media: "
                        + ", ".join(available)
                        + ". Only those, and outputs/ files comfy_download brought back in this "
                        "conversation, can go to the instance."
                    )
                    continue
                server, err = _push_reference(located, subfolder)
                if err:
                    failures.append(f"{path}: {err}")
                    continue
                uploaded[path] = server
                import studio_state

                studio_state.mark_uploaded(server)  # a name a loader may now legitimately carry

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
                # THIS CHAT'S outputs/ FOLDER, like its references (chat_paths): the window lists
                # exactly that folder, and another conversation's renders stay out of it.
                rel = f"{chat_paths.chat_rel(chat_paths.OUTPUTS)}/{Path(filename).name}"
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
                studio_state.mark_downloaded(rel)  # so comfy_upload may send it back up

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

    _lease(_WORK_LEASE_S)  # a render in flight is work, however quiet the platform side is
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


def _api_node_flags(prompt: dict) -> dict[str, bool]:
    """class_type -> the instance's `api_node` flag, for every class in the graph whose spec
    could be read.

    THE INSTANCE DECIDES WHAT IS PAID; THE TABLE ONLY SAYS HOW MUCH. partner-nodes.json finds a
    provider by a prefix of the class name, and a prefix cannot tell a partner node from a free
    core node that shares its first word: `FluxKontextMultiReferenceLatentMethod` is ComfyUI's
    own reference-conditioning node, and matched as "Flux" it priced a local Qwen graph as a
    $7.72 BFL video job — refused by the credit gate three chats running, with the model told to
    "offer a free alternative" to a graph that was already free. Every partner node's spec
    carries `api_node: true`; the core nodes do not. One small GET per unique class — the same
    calls the leak check below always made for the classes the table did not price.

    A class whose spec cannot be read is absent from the result — neither free nor paid — so
    the table's prefix still judges it, which errs toward charging."""
    flags: dict[str, bool] = {}
    classes = sorted({str(nd.get("class_type") or "") for nd in (prompt or {}).values()
                      if isinstance(nd, dict)} - {""})
    for cls in classes:
        res = _get(f"/api/object_info/{cls}")
        if not res.ok:
            continue  # an unknown class fails validation on its own terms; not this gate's job
        try:
            spec = (res.json() or {}).get(cls) or {}
        except ValueError:
            continue
        flags[cls] = bool(spec.get("api_node"))
    return flags


def _free_classes(flags: dict[str, bool]) -> set[str]:
    return {cls for cls, paid in flags.items() if not paid}


def _quote_for(prompt: dict, flags: dict[str, bool]):
    """Price a graph, or None when pricing is unavailable.

    A BROKEN TABLE MUST NOT BILL. If the file is missing or malformed this returns None, and the
    caller refuses any graph containing partner nodes rather than running them unpriced.
    """
    try:
        import partner_pricing

        return partner_pricing.price_workflow(prompt, free_classes=_free_classes(flags))
    except Exception:  # noqa: BLE001
        return None


def _unpriced_partner_nodes(flags: dict[str, bool], quote) -> list[str]:
    """Partner nodes the TABLE never heard of — the leak the table cannot close by itself.

    A provider Comfy added last week (ByteDance, Gemini image, hosted Wan, for months) matched
    no prefix, priced as a free local node, and ran on the platform's own Comfy balance while
    the user was charged zero. The instance says which classes are partner nodes; any of those
    the quote did not price is a refusal upstairs."""
    priced = {i.class_type for i in getattr(quote, "items", [])} | set(getattr(quote, "unpriced", []))
    return sorted(cls for cls, paid in flags.items() if paid and cls not in priced)


def _charge(credits: int, note: str, usd: float = 0.0) -> tuple[bool, str]:
    """Debit the caller's credits for a paid run. (ok, message).

    CHARGED AFTER A SUCCESSFUL SUBMIT, gated BEFORE it. `/debit` drains a partial balance rather
    than refusing, which is right for cheap model calls — the call already ran, so refusing only
    leaves the balance untouched and the pre-call gate never engages. One video generation is
    ~140 credits, two orders of magnitude larger, so the gate is what does the real work here and
    this is only the settlement.
    """
    account_id = current_account_id()
    if not account_id or credits <= 0:
        return True, ""
    res = fetch(
        f"{_ACCOUNTS}/debit",
        method="POST",
        # DOLLARS, NOT CREDITS, when we have them: the platform converts at its own rate, so the
        # charge is right even if the rate quoted a moment ago was the default.
        json={
            "account_id": account_id,
            "agent_id": "comfy-artchitect",
            **({"usd": round(float(usd), 6)} if usd > 0 else {"credits": int(credits)}),
        },
        headers=_AUTH,
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
    account_id = current_account_id()
    if not account_id or credits <= 0:
        return True, ""
    res = fetch(
        f"{_ACCOUNTS}/budget/{account_id}",
        headers=_AUTH,
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

            # NOTHING RENDERS BEFORE THE USER HAS ANSWERED THE CHECKPOINT — mechanically. The
            # daemon stamps the checkpoint when a turn ends on one and the answer when the next
            # message arrives; this tool reads the stamp. See studio_state.checkpoint_answered.
            import studio_state

            answered, why = studio_state.checkpoint_answered(_workflow_name(path))
            if not answered:
                return ToolResult.text(why, is_error=True)

            # REFERENCE SLOTS ARE FILLED HERE, MECHANICALLY — see reference_slots. Every @role
            # token resolves to the file the user put in that slot, the files go up, the server
            # names go in. An empty slot is a refusal that names it: nothing runs on a guess, and
            # nothing here is a matter of the model remembering to upload.
            ws = Path(current_workspace(".") or ".")
            roles = reference_slots.roles_in(prompt)
            if roles:
                problems = reference_slots.bad_roles(prompt)
                if problems:
                    return ToolResult.text(
                        "bad reference slot(s):\n  " + "\n  ".join(problems), is_error=True
                    )
                filled, missing = reference_slots.status(ws, roles)
                if missing:
                    return ToolResult.text(
                        "waiting for reference(s). The user adds them in the References panel, "
                        "and this refuses until every slot is filled:\n"
                        + reference_slots.describe(ws, list(roles))
                        + "\nNothing for you to do about it: say which slots are empty, end the "
                        "turn, and run again when they are filled.",
                        is_error=True,
                    )
                names: dict[str, str] = {}
                for role, rel in filled.items():
                    server, err = _push_reference(rel)
                    if err:
                        return ToolResult.text(
                            f"could not upload {rel} for {reference_slots.TOKEN}{role}: {err}",
                            is_error=True,
                        )
                    names[role] = server
                prompt = reference_slots.bind(prompt, names)

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
            # PAID PARTNER NODES: the instance says which are paid, the table says how much;
            # price, gate, then submit with the platform's key.
            flags = _api_node_flags(prompt)
            quote = _quote_for(prompt, flags)
            body: dict = {"prompt": prompt}
            charge_credits = 0
            charge_usd = 0.0
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
            elif (leak := _unpriced_partner_nodes(flags, quote)):
                # THE SECOND LAYER — see _unpriced_partner_nodes. The instance says these are
                # partner nodes; the table has no provider for them; so they would have run
                # unpriced. Refused, naming them, exactly like an unpriced model.
                return ToolResult.text(
                    "this workflow uses partner node(s) the price table has no provider for, so "
                    "it will not be submitted: " + ", ".join(leak)
                    + ". comfy_price lists what is priced; the operator adds providers in "
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
                # PLATFORM CREDITS, converted from the quote's dollars — the balance gate below
                # compares them with a balance in the same unit.
                charge_usd = quote.usd
                charge_credits = _platform_credits(charge_usd)
                affordable, why = _affordable(charge_credits)
                if not affordable:
                    return ToolResult.text(why, is_error=True)
                # THE KEY GOES IN extra_data AS A PLACEHOLDER — see the note above. If the
                # deployment holds no key the host leaves it literal and the partner node
                # answers 401 with the name visible, which is a debuggable failure rather than
                # a silent one.
                body["extra_data"] = {"api_key_comfy_org": _COMFY_KEY_REF}

            _lease(_WORK_LEASE_S)
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
                charged, problem = _charge(charge_credits, "this run", usd=charge_usd)
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


async def _settle_downloads(abort, on_update, what: str) -> str:
    """If ComfyUI-Manager is mid-download, hold until it is done; the note for the report.
    '' when nothing was in flight.

    THE ONE WAIT EVERY TOOL THAT READS THE INSTANCE'S FILES SHARES. This used to be a NOTE —
    "Manager is STILL DOWNLOADING 1 file(s) — wait and re-check before concluding it failed" —
    and a model has no way to wait except to call a tool again, so it called comfy_inventory
    again, and again, with a different filter word each time (which is why the repeat-call
    brake never tripped), at a 145k-token model call a time. A sentence asking the model to
    wait is the same mistake comfy_install made before it held its own line; now inventory and
    validate hold theirs, with the same declared timeouts, and answer once the queue is empty.
    The guard's retry re-enters and waits again if an attempt runs out."""
    info = _queue_info()
    if not info or not _queue_busy(info):
        return ""
    total, done = info.get("total_count"), info.get("done_count")
    state, waited = await _hold_until_manager_idle(
        abort, on_update, f"{what}: waiting for ComfyUI-Manager to finish downloading ({done}/{total} done)"
    )
    if state == "idle":
        return f"(waited {_clock(waited)} for ComfyUI-Manager to finish {total} download(s))"
    if state == "aborted":
        return f"(stopped after {_clock(waited)}; ComfyUI-Manager is still downloading)"
    return (
        f"(lost ComfyUI-Manager after {_clock(waited)} while it was downloading — a file missing "
        "above may be that; gpu_ensure if the GPU was reclaimed, then check again)"
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


def _queue_info() -> dict:
    st = _get("/manager/queue/status", timeout_s=15.0)
    try:
        return st.json() if st.ok and st.text.strip() else {}
    except ValueError:
        return {}


def _queue_busy(info: dict) -> bool:
    return bool(info.get("is_processing")) or int(info.get("in_progress_count") or 0) > 0


def _clock(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}m{s:02d}s" if m else f"{s}s"


async def _hold_until_manager_idle(abort, on_update, what: str) -> tuple[str, float]:
    """Hold this call open until ComfyUI-Manager's queue is empty.
    Returns ("idle" | "aborted" | "unreachable", seconds waited).

    THE WAIT IS THE TOOL'S, NOT THE MODEL'S. This used to poll for 75 s and then return SUCCESS
    saying "the download continues, do other work, confirm with comfy_inventory" — a sentence in
    a prompt asked to do a tool's job, and the model did what any model does with a success
    result: reported the install done and ended its turn, leaving the person to open vast.ai and
    see whether a 4 GB file had actually landed. Now the call comes back when the queue is
    empty. While it waits it keeps the GPU's idle lease alive (the reaper once destroyed a box
    with four downloads in flight) and hands the window a progress line. A slice that runs out
    is the guard's timeout, which retries into a fresh call — see _WAIT_ATTEMPT_S."""
    started = time.monotonic()
    last_lease = started
    last_notice = 0.0
    failures = 0
    step = _POLL_S
    # The first look comes AFTER a beat: Manager reports idle for an instant between
    # /queue/start and its worker picking the job up, and that instant read as "done".
    await asyncio.sleep(step)
    while True:
        if abort.is_set():
            return "aborted", time.monotonic() - started
        info = _queue_info()
        if not info:
            failures += 1
            if failures >= _STATUS_FAILURES_MAX:
                return "unreachable", time.monotonic() - started
        else:
            failures = 0
            if not _queue_busy(info):
                return "idle", time.monotonic() - started
        now = time.monotonic()
        if now - last_lease >= _LEASE_EVERY_S:
            _lease(_WORK_LEASE_S)
            last_lease = now
        if on_update is not None and now - last_notice >= _NOTICE_EVERY_S:
            total = info.get("total_count")
            tally = f", Manager {info.get('done_count')}/{total} done" if total else ""
            on_update(ToolResult.text(f"{what} — {_clock(now - started)}{tally}"))
            last_notice = now
        await asyncio.sleep(step)
        step = min(step * 1.3, _POLL_MAX_S)


def _loadable_names() -> dict[str, str]:
    """basename -> the name a loader lists it under, for every model file on the instance.
    Manager files a download under a subfolder (`qwen-image-edit/<file>`), and that subfoldered
    name is what the loader's enum carries and what the workflow must say — a graph naming the
    bare file validates as "missing" against an instance that has it."""
    inv = _get("/api/object_info", timeout_s=60.0)
    out: dict[str, str] = {}
    try:
        if inv.ok:
            for _c, _i, files in _model_enums(inv.json()):
                for name in files:
                    out.setdefault(Path(str(name)).name.lower(), str(name))
    except ValueError:
        pass
    return out


async def _await_loadable(filenames: list[str], abort) -> dict[str, str]:
    """filename -> loader name, for those of `filenames` a loader lists within the rescan grace."""
    started = time.monotonic()
    want = {f.lower() for f in filenames}
    found: dict[str, str] = {}
    while True:
        listed = _loadable_names()
        for f in filenames:
            if f.lower() in listed:
                found[f] = listed[f.lower()]
        if len(found) == len(want) or abort.is_set():
            return found
        if time.monotonic() - started >= _LOADABLE_GRACE_S:
            return found
        await asyncio.sleep(_POLL_S)


async def _await_instance(abort) -> float | None:
    """Seconds until a restarting ComfyUI answers again, or None past _REBOOT_WAIT_S."""
    started = time.monotonic()
    await asyncio.sleep(_POLL_S * 2)  # it goes DOWN first; an immediate 200 is the old process
    while time.monotonic() - started < _REBOOT_WAIT_S:
        if abort.is_set():
            return None
        if _get("/api/system_stats", timeout_s=10.0).ok:
            return time.monotonic() - started
        await asyncio.sleep(_POLL_S)
    return None


def _install_requests(params: dict) -> list[dict]:
    """The files to install: `files` as declared, or the one-file triple older transcripts carry."""
    raw = params.get("files")
    items = list(raw) if isinstance(raw, list) else []
    if not items and params.get("filename"):
        items = [{"filename": params.get("filename"), "url": params.get("url"), "kind": params.get("kind")}]
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        filename = str(it.get("filename") or "").strip()
        url = str(it.get("url") or "").strip()
        kind = str(it.get("kind") or "").strip().lower()
        if filename and url and kind:
            out.append({"filename": filename, "url": url, "kind": kind})
    return out


class ComfyInstallTool(Tool):
    name = "comfy_install"
    label = "Install models on the instance"
    # THE WAIT IS DECLARED, so both clocks that could cut it know. The engine's guard times ONE
    # attempt out at _WAIT_ATTEMPT_S and retries into a fresh one (retryable + retry_on_timeout —
    # the guard retries only its own timeouts and transient exceptions, never an error result,
    # so "not in Manager's catalog" is still answered once), and the sandbox's clock follows the
    # same declaration (capabilities._timeout_for) instead of killing the child at 120 s.
    default_timeout_sec = _WAIT_ATTEMPT_S
    default_retryable = True
    default_retry_on_timeout = True
    default_max_retries = _WAIT_ATTEMPTS
    description = (
        "Download model files onto the user's ComfyUI instance — WITHOUT asking them to touch a "
        "terminal — through Manager or the platform GPU downloader, chosen automatically. "
        "Give EVERY file comfy_validate listed, in ONE call: filename, its download URL "
        "(comfy_research finds these on Hugging Face/Civitai) and its kind (checkpoint, unet, "
        "vae, text_encoder, lora, controlnet, upscale…). The call returns when the files are "
        "LOADABLE: it holds the line while the GPU downloads (minutes for a multi-GB weight, "
        "progress shown as it goes) and comes back with 'installed' — naming the exact loader "
        "name to put in the workflow — or with the reason it could not. Nothing to poll, nothing "
        "to re-check afterwards. This is how you FIX a missing-model workflow yourself instead "
        "of handing the user a list. Catalogued files use Manager; uncatalogued public Hugging "
        "Face safetensors automatically download on the GPU. Never lower Manager security. "
        "Failures are reported immediately; success means the file is actually loadable."
    )
    parameters = {
        "type": "object",
        "required": ["files"],
        "properties": {
            "files": {
                "type": "array",
                "minItems": 1,
                "description": "comfy_validate's whole missing-file list, in one call.",
                "items": {
                    "type": "object",
                    "required": ["filename", "url", "kind"],
                    "properties": {
                        "filename": {
                            "type": "string",
                            "description": "The exact filename to save as, e.g. 'wan2.2_vae.safetensors'.",
                        },
                        "url": {
                            "type": "string",
                            "description": "Direct download URL (a Hugging Face /resolve/ link, a "
                            "Civitai download URL for catalogued models). Uncatalogued files "
                            "require a public HF /resolve/ URL whose basename matches filename.",
                        },
                        "kind": {
                            "type": "string",
                            "description": "Where it belongs: checkpoint | unet | diffusion_model | "
                            "vae | text_encoder | clip | lora | controlnet | upscale.",
                        },
                    },
                },
            }
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            files = _install_requests(params)
            if not files:
                return ToolResult.text("files must contain {filename, url, kind} entries", is_error=True)
            for file in files:
                allowed, why = studio_state.install_allowed(file["filename"])
                if not allowed:
                    return ToolResult.text(why, is_error=True)

            def report(message):
                if on_update:
                    on_update(ToolResult.text(message))

            def connection():
                response = fetch(
                    f"{_ACCOUNTS}/vast/download-connection", method="POST", headers=_AUTH,
                    json={"account_id": current_account_id()}, timeout_s=30,
                )
                if not response.ok:
                    raise ValueError(f"GPU download connection unavailable (HTTP {response.status}); "
                                     "accounts service must support /vast/download-connection")
                return response.json()

            def submit(file, entry):
                body = {"ui_id": f"agent-{file['filename']}",
                        "filename": entry["filename"], "url": entry.get("url") or file["url"],
                        "save_path": entry.get("save_path"), "type": entry.get("type"),
                        "base": entry.get("base", ""), "name": entry.get("name", "")}
                result = _post("/manager/queue/install_model", body, timeout_s=30)
                if not result.ok:
                    raise ValueError(_failed(result, f"install {file['filename']}"))

            def start_manager():
                result = _post("/manager/queue/start", None, timeout_s=15)
                if not result.ok:
                    raise ValueError(_failed(result, "start Manager downloads"))

            async def wait_manager(abort, report):
                state, _ = await _hold_until_manager_idle(
                    abort, lambda value: report(value.content[0].text), "downloading models"
                )
                return state

            direct = GpuModelDownloadClient(
                fetch=fetch, connection=connection, current_connection=_override, get=_get,
                lease=lambda: _lease(_WORK_LEASE_S),
            )
            installer = ModelInstallationService(
                catalog=_manager_catalog, loadable=_loadable_names, submit=submit,
                start_manager=start_manager, manager_busy=lambda: _queue_busy(_queue_info()),
                queued_recently=lambda filename: time.time() - studio_state.queued_at(filename) < _QUEUED_MEMORY_S,
                mark_queued=studio_state.mark_queued, wait_manager=wait_manager,
                await_loadable=_await_loadable, lease=lambda: _lease(_WORK_LEASE_S), direct=direct,
            )
            installed = await installer.install(files, abort, report)
            return ToolResult.text(
                "Installed and loadable: " + "; ".join(f"{f} -> '{name}'" for f, name in installed.items())
                + ". Re-validate with those names, then run."
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
    # The same declared wait as comfy_install — see there.
    default_timeout_sec = _WAIT_ATTEMPT_S
    default_retryable = True
    default_retry_on_timeout = True
    default_max_retries = _WAIT_ATTEMPTS
    description = (
        "Install a ComfyUI CUSTOM NODE PACK on the user's instance — IPAdapter, PuLID, a LoRA "
        "trainer, video helpers, anything in ComfyUI-Manager's registry — WITHOUT asking the user "
        "to touch Manager themselves. Give the pack's registry id, its title, or its GitHub URL "
        "(research and `comfy_validate`'s missing-node report both give you these). It installs "
        "through ComfyUI-Manager, restarts ComfyUI so the new nodes load, and returns once the "
        "instance answers again — then comfy_node_spec the class to confirm. This is how you fix "
        "a `missing_node_type` / unknown-node-class yourself. Node packs are code: say which one "
        "you are installing and why before you call this."
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
            # A PACK IS INSTALLED BECAUSE A VALIDATION ASKED FOR ONE — see
            # studio_state.node_install_allowed. Also what stops "install a fake pack to force
            # a restart".
            import studio_state

            allowed, why = studio_state.node_install_allowed()
            if not allowed:
                return ToolResult.text(why, is_error=True)
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
            _lease(_WORK_LEASE_S)
            _post("/manager/queue/start", None, timeout_s=15.0)

            # The same hold as comfy_install: this returns when Manager is done, not before.
            state, waited = await _hold_until_manager_idle(abort, on_update, f"installing {title}")
            if state == "aborted":
                return ToolResult.text(
                    f"stopped after {_clock(waited)}; Manager continues installing {title}. Call "
                    "comfy_node_install again to finish (restart included)."
                )
            if state == "unreachable":
                return ToolResult.text(
                    f"lost ComfyUI-Manager after {_clock(waited)} while installing {title}. If "
                    "the GPU was reclaimed, gpu_ensure; then call comfy_node_install again.",
                    is_error=True,
                )

            if not (params.get("restart", True)):
                return ToolResult.text(
                    f"installed {title} ({pack_id}). ComfyUI must RESTART before its nodes load — "
                    "call comfy_node_install again with restart, or ask the user to restart."
                )
            # A pack that is installed but not loaded is still a missing node. Rebooting is the
            # step that makes it real, and Manager owns it — and so is waiting for the instance
            # to answer again, which used to be "call comfy_probe until it answers".
            _post("/manager/reboot", None, timeout_s=20.0)
            if on_update is not None:
                on_update(ToolResult.text(f"installed {title}; restarting ComfyUI"))
            back = await _await_instance(abort)
            if back is None:
                return ToolResult.text(
                    f"installed {title} ({pack_id}) and restarted ComfyUI, but the instance has "
                    f"not answered in {_clock(_REBOOT_WAIT_S)}. comfy_probe to see whether it is "
                    "back; if not, the restart may have failed on the instance.",
                    is_error=True,
                )
            return ToolResult.text(
                f"installed {title} ({pack_id}) and restarted ComfyUI — it answers again "
                f"({_clock(back)} to come back). comfy_node_spec the node class you need to "
                "confirm it loaded before emitting a workflow that uses it."
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
    # The same declared wait as comfy_install: validating while Manager is downloading holds
    # until the download lands, then compiles against what actually landed (_settle_downloads).
    default_timeout_sec = _WAIT_ATTEMPT_S
    default_retryable = True
    default_retry_on_timeout = True
    default_max_retries = _WAIT_ATTEMPTS
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
            },
            "reference_workflow_url": {
                "type": "string",
                "description": "Raw publisher/ComfyUI reference workflow JSON URL (GitHub or HF). "
                "Required for a new model stack with separate VAE/text encoders; reused for unchanged stacks. "
                "The tool fetches it and rejects incompatible companion filenames before authorising installs.",
            },
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

            # Invalidate the old shopping list FIRST. A failed reference fetch/check must not
            # leave a previous validation authorising the wrong companion file.
            studio_state.forget_validated(_workflow_name(path))
            reference_problems = WorkflowReferenceRepository(
                Path(current_workspace(".") or "."), fetch=fetch,
            ).check(graph, str(params.get("reference_workflow_url") or "").strip())
            if reference_problems:
                return ToolResult.text("Model stack is not verified:\n" + "\n".join(reference_problems), is_error=True)

            settled = await _settle_downloads(abort, on_update, "validate")
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
            # The same facts without the prose, for the record the install gates read.
            raw_unknown: list[str] = []
            raw_missing: list[str] = []
            bad_enums: list[str] = []
            bad_links: list[str] = []
            for nid, entry in graph.items():
                if not isinstance(entry, dict):
                    continue
                cls = str(entry.get("class_type") or "")
                spec = catalogue.get(cls)
                if not isinstance(spec, dict):
                    unknown_nodes.append(f"node {nid}: class '{cls}' does not exist here")
                    raw_unknown.append(str(cls))
                    continue
                sections = spec.get("input") or {}
                specs = {
                    name: s
                    for section in ("required", "optional")
                    for name, s in (sections.get(section) or {}).items()
                }
                for field, value in (entry.get("inputs") or {}).items():
                    try:
                        link = WorkflowLink.from_input(value, graph)
                    except ValueError as exc:
                        bad_links.append(f"node {nid}.{field} {exc}")
                        continue
                    if link is not None:
                        continue
                    if reference_slots.role_of(value) is not None:
                        continue  # a reference slot — filled and checked at run time, listed below
                    s = specs.get(field)
                    choices = s[0] if isinstance(s, list) and s else None
                    if not isinstance(choices, list) or not isinstance(value, str):
                        continue
                    if value in choices:
                        continue
                    # A MISSING WEIGHT IS A SHOPPING-LIST ITEM, NOT AN INVALID VALUE. The field's
                    # NAME says it is a weights slot; what is installed in it says nothing. On a
                    # fresh box the VAE enum is one sentinel, `pixel_space`, so judged by its
                    # contents `qwen_image_vae.safetensors` read as "not one of [pixel_space]",
                    # comfy_install refused it (not on the list), and the only legal value left
                    # was `pixel_space` — which validates, then renders a raw latent or runs out
                    # of memory. The model diagnosed that, re-emitted the right file, and was
                    # refused again. Same rule _hide_installed_files already applies.
                    if field in _MODEL_FIELDS or _looks_like_model_list(choices) or (
                        not choices and value.lower().endswith(_MODEL_EXTS)
                    ):
                        missing_files.append(f"{value}  (for {cls}.{field}, node {nid})")
                        raw_missing.append(str(value))
                    else:
                        legal = ", ".join(str(c) for c in choices[:8])
                        bad_enums.append(
                            f"node {nid}.{field}: '{value}' is not one of [{legal}…]"
                        )

            downloading = settled
            # REFERENCE SLOTS, with their state. Not a compile error either way: an empty slot is
            # the user's move, in the References panel, and comfy_run refuses until it is made.
            bad_enums += reference_slots.bad_roles(graph)
            slot_roles = list(reference_slots.roles_in(graph))
            slots_note = ""
            if slot_roles:
                ws = Path(current_workspace(".") or ".")
                slots_note = (
                    "\nreference slots (the user fills them in the References panel; comfy_run "
                    "refuses while any is EMPTY):\n" + reference_slots.describe(ws, slot_roles)
                )
            # THE RECORD THE INSTALL GATES READ. Written whether or not the graph compiles: a
            # clean validation is also a fact ("nothing to install"), and comfy_install answers
            # from this and nothing else.
            try:
                studio_state.mark_validated(_workflow_name(path), raw_missing, raw_unknown)
            except Exception:  # noqa: BLE001 — the report still goes out
                pass
            if not (unknown_nodes or missing_files or bad_enums or bad_links):
                return ToolResult.text(
                    f"compiles: all {len(graph)} node(s) exist on this instance, links resolve, "
                    "and every model file it names is loadable. Safe to comfy_run."
                    + (f"\n{downloading}" if downloading else "")
                    + slots_note
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
            if slots_note:
                lines.append(slots_note.strip())
            return ToolResult.text("\n".join(lines), is_error=True)
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"comfy_validate failed: {type(e).__name__}: {e}", is_error=True)


class ComfyPriceTool(Tool):
    name = "comfy_price"
    label = "What the paid models cost"
    default_retryable = True
    description = (
        "What ComfyUI's paid partner nodes cost, in credits. Call it BEFORE choosing between a "
        "paid and a free route, and before the ask (ask_user), so the user is shown real numbers "
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
                    "An emitted workflow to price exactly: the path comfy_emit returned, or just"
                    " its name. Omit to list the whole catalogue."
                ),
            }
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            import partner_pricing

            table = partner_pricing.load_table()
            rate = _platform_rate()
            name = str(params.get("workflow") or "").strip()
            if not name:
                return ToolResult.text(
                    self._catalogue(table, rate), details={"table": table, "credits_per_usd": rate}
                )

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
            # THE SAME ANSWER comfy_run will give: the instance says which nodes are paid. With
            # no instance up, the table's prefixes judge alone — which can only over-quote.
            quote = partner_pricing.price_workflow(
                graph, table, free_classes=_free_classes(_api_node_flags(graph))
            )
            platform = quote.platform_credits(rate)
            head = (
                f"{path.name}: ≈${quote.usd:.2f} → {platform:,} credits"
                if quote.paid and quote.ok
                else f"{path.name}:"
            )
            return ToolResult.text(
                head + "\n" + quote.as_text(platform_rate=rate),
                details={
                    "usd": round(quote.usd, 4),
                    "credits": platform,
                    "credits_per_usd": rate,
                    "ok": quote.ok,
                    "paid": quote.paid,
                },
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"comfy_price failed: {type(e).__name__}: {e}", is_error=True)

    @staticmethod
    def _catalogue(table: dict, rate: float) -> str:
        """Every paid model, cheapest first WITHIN each provider, as DOLLARS and platform
        credits.

        Sorted by price rather than listed in file order, because this is read while choosing —
        and an unsorted list quietly nudges toward whichever entry happens to be first. Dollars
        because that is what the table and the provider mean; credits because that is what the
        person's balance is in, and a quote in a unit the balance is not in reads as free.
        """
        import math

        markup = float(table.get("markup") or 1.0)
        per = float(table.get("credits_per_usd") or 100.0)
        lines = [
            "Paid providers available through ComfyUI's partner nodes, as dollars of provider "
            f"cost (incl. {markup:g}x) and the credits charged to the user at "
            f"{rate:,.0f} credits per dollar. No API key is needed from them.",
        ]

        def money(comfy_credits: float) -> str:
            usd = comfy_credits * markup / per
            return f"${usd:.3f} → {int(math.ceil(usd * rate)):,} credits"

        for provider in table.get("providers") or []:
            unit = str(provider.get("unit") or "per_run")
            rows = []
            for model, rates in (provider.get("models") or {}).items():
                numeric = {k: v for k, v in rates.items() if not str(k).startswith("_") and k != "classes"}
                numeric["_default"] = rates.get("_default", max(numeric.values()) if numeric else 0.0)
                cheapest = min(float(v) for v in numeric.values())
                rows.append((cheapest, model, numeric, str(rates.get("_unit") or unit)))
            rows.sort()
            lines.append(f"\n{provider.get('label')} ({unit.replace('per_', 'per ')}):")
            for cheapest, model, numeric, model_unit in rows:
                suffix = "/second of video" if model_unit == "per_second" else "/run"
                tiers = [k for k in numeric if k != "_default"]
                extra = f"  [{', '.join(tiers)}]" if tiers else ""
                lines.append(f"   {model}: {money(cheapest)}{suffix}{extra}")
        lines.append(
            f"\nA 5-second Kling clip at 720p is about {money(17.72 * 5)}; one Flux Ultra image "
            f"about {money(12.66)}. QUOTE THE CREDITS ONLY — never the dollars, in the ask or in "
            f"your prose. The user holds a credit balance and is billed in credits; the dollar "
            f"figure above is the PLATFORM's provider cost, and showing both only raises the "
            f"question of which one they are paying."
        )
        return "\n".join(lines)


def _workflow_name(path) -> str:
    """`workflows/<chat>/storyboard.api.json` -> `storyboard`: the ROLE name comfy_emit was
    given, which is what every per-workflow record is keyed by."""
    base = Path(str(path)).name
    return base[:-9] if base.endswith(".api.json") else (base[:-5] if base.endswith(".json") else base)


def _workflow_path(name: str):
    """An emitted workflow by name, however loosely the caller named it: the path comfy_emit
    returned (`workflows/<chat>/x.api.json`), or just `x`. A bare name is looked for in THIS
    chat's folder (chat_paths) — another conversation's workflow is not this job's."""
    root = Path(current_workspace(".") or ".")
    candidate = Path(name)
    if candidate.is_absolute():
        return candidate
    if name.startswith(f"{chat_paths.WORKFLOWS}/"):
        return root / candidate
    stem = name[:-9] if name.endswith(".api.json") else name.removesuffix(".json")
    folder = chat_paths.chat_rel(chat_paths.WORKFLOWS)
    for guess in (f"{folder}/{stem}.api.json", f"{folder}/{stem}.json", name):
        path = root / guess
        if path.exists():
            return path
    return root / f"{folder}/{stem}.api.json"


def register(api, ctx):
    # Imported by bare name: the loader puts this plugin's folder on sys.path, so siblings are
    # top-level modules here rather than a package.
    from comfy_emit import ComfyEmitTool
    from comfy_research import ComfyResearchTool
    from comfy_delete import ComfyDeleteTool

    api.register_tool(ComfyInstallTool())
    api.register_tool(ComfyNodeInstallTool())
    api.register_tool(ComfyProbeTool())
    api.register_tool(ComfyEmitTool())
    api.register_tool(ComfyValidateTool())
    api.register_tool(ComfyInventoryTool())
    api.register_tool(ComfyNodeSpecTool())
    api.register_tool(ComfyNodeSearchTool())
    api.register_tool(ComfyResearchTool())
    api.register_tool(ComfyUploadTool())
    api.register_tool(ComfyReferenceAssignTool())
    api.register_tool(ComfyDownloadTool())
    api.register_tool(ComfyPriceTool())
    api.register_tool(ComfyRunTool())
    api.register_tool(ComfyRunStatusTool())
    api.register_tool(ComfyStudioStateTool())
    api.register_tool(ComfyInterruptTool())
    api.register_tool(ComfyDeleteTool())

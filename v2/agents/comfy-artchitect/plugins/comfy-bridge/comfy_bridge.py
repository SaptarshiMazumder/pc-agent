"""ComfyUI bridge — the agent's only route to a running instance.

EVERY REQUEST GOES THROUGH THE HOST. `fetch` is the brokered call: this module never opens a
socket, never reads an environment variable, never spawns anything.

WHICH COMFYUI — COMFY CLOUD, ALWAYS. Every call goes to https://cloud.comfy.org with the person's
Comfy API key (ComfyCloudConnection). It is serverless: nothing is rented, started, leased or
stopped, and a job is billed to their Comfy plan for the GPU seconds it runs. Nothing checks the
connection ahead of time — a missing key or plan fails the first call that needs it, in Comfy
Cloud's own words. (`comfy_research`, in its own module, uses the same brokered `fetch` to reach
Hugging Face and Civitai.)

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
    current_run_context,
    current_workspace,
)
from agent_runtime.infrastructure.net.outbound import fetch

import chat_paths
import reference_slots
import studio_state
from comfy_cloud_connection import ComfyCloudConnection
from comfy_cloud_model_importer import ComfyCloudModelImporter
from editor_graph_converter import EditorGraphConverter
from model_readiness import ModelReadiness
from workflow_link import WorkflowLink
import node_input_schema
from workflow_reference_repository import WorkflowReferenceRepository
from workflow_dependency_repository import WorkflowDependencyRepository
from workflow_installer_exporter import WorkflowInstallerExporter
from family_rule_validator import FamilyRuleValidator, RuleReport
from family_structural_checks import FamilyStructuralChecks
from graph_structural_validator import GraphStructuralValidator
from knowledge_base_catalog import KnowledgeBaseCatalog
from node_registry_cache import NodeRegistryCache

#: The model families the agent knows, and the node catalogue it ships for the pinned ComfyUI.
_KNOWLEDGE_BASE = Path(__file__).parent / "knowledge_base"


#: HOW A WAITING TOOL WAITS. A model import is minutes of nothing to do; comfy_install holds its
#: call open until the files are loadable. Module-level so a verifier can shrink them.
_POLL_S = 5.0
_LOADABLE_GRACE_S = 60.0  # the lag between "imported" and "a loader lists it"
#: ONE ATTEMPT of a waiting tool. Under the hosted executor's 900 s cap (infra
#: `executor_timeout_seconds`, a Lambda) with room for its transfers. The engine's guard times
#: an attempt out at exactly this and — because the tool declares retry_on_timeout — starts
#: another, which re-enters here and WAITS for the files it already queued instead of queueing
#: them again (studio_state.queued_at). Four attempts is close to an hour of download on any
#: backend; a desktop subprocess has no cap of its own and is sliced the same way, so the two
#: paths behave identically.
_WAIT_ATTEMPT_S = 840.0
_WAIT_ATTEMPTS = 4


def _looks_like_model_list(values) -> bool:
    """An enum whose entries are model FILENAMES, as opposed to sampler names or booleans."""
    return ModelReadiness.looks_like_model_list(values)


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


def _override() -> dict:
    """The ComfyUI every call goes to: Comfy Cloud ({kind, url})."""
    return ComfyCloudConnection.record()


def _headers() -> dict:
    """The person's Comfy API key, as a name the host fills in (ComfyCloudConnection)."""
    return ComfyCloudConnection.headers()


def _url(path: str) -> str:
    return ComfyCloudConnection.url(path)


class _NoInstance:
    """A failed response for "there is no instance yet", shaped like a real one.

    Returned rather than raised so every caller's existing `if not res.ok` handling reports it
    the same way it reports any other failure — one error path, not two.
    """

    ok = False
    status = 0
    text = ""

    def __init__(self) -> None:
        self.error = _no_instance_message()

    def json(self):
        return {}


#: The platform, by NAME — the host substitutes it: where comfy_price reads the credit rate. NOT
#: `accounts.api_base()`, which is daemon-process state and reads as empty inside a sandbox.
_ACCOUNTS = "${AGENTD_ACCOUNTS_URL}"

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


def _no_instance_message() -> str:
    return ("Comfy Cloud is not reachable from here. The person's Comfy API key (Settings) and a paid "
            "Comfy Cloud plan are what it needs; say so in one line and stop.")


def _no_instance() -> "ToolResult":
    """The same guidance _NoInstance carries, as a tool result.

    UPLOAD AND DOWNLOAD BUILD THEIR OWN FETCH CALLS — one posts a multipart file, the other
    appends a query — so neither goes through `_get`/`_post` and neither got the empty-URL guard.
    They passed "" straight to the broker, which refused with "a fetch request needs a url": true,
    unactionable, and nothing to do with the real problem, which is that no GPU is running.
    """
    return ToolResult.text(_no_instance_message(), is_error=True)


def _get(path: str, timeout_s: float = 30.0):
    url = _url(path)
    return fetch(url, headers=_headers(), timeout_s=timeout_s) if url else _NoInstance()


def _post(path: str, body, timeout_s: float = 60.0):
    url = _url(path)
    if not url:
        return _NoInstance()
    return fetch(url, method="POST", json=body, headers=_headers(), timeout_s=timeout_s)


def _failed(res, what: str) -> str:
    """One sentence naming what went wrong, in Comfy Cloud's own words where there are any."""
    if res.error:
        return f"{what}: could not reach Comfy Cloud ({res.error})."
    if res.status in (401, 403):
        return (f"{what}: Comfy Cloud refused the key (HTTP {res.status}: {(res.text or '')[:200]}). "
                "The person's Comfy API key (Settings, from platform.comfy.org) and a paid Comfy Cloud "
                "plan are needed — tell them that in one line.")
    if res.status == 402:
        return f"{what}: Comfy Cloud says the plan has no credits left (HTTP 402) — tell the person."
    return f"{what}: HTTP {res.status} — {(res.text or '')[:300]}"


class ComfyProbeTool(Tool):
    name = "comfy_probe"
    label = "Probe ComfyUI"
    default_retryable = True
    description = (
        "Ask Comfy Cloud what it runs (ComfyUI version) and keep its node list for checking designs. "
        "Not required before anything else — every tool reports Comfy Cloud's own error if the key "
        "or plan is missing."
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
                f"(python {(str(system.get('python_version') or '?').split() or ['?'])[0]}, "
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
            lines.append(_capture_node_registry(str(system.get("comfyui_version") or "")))
            return ToolResult.text("\n".join(x for x in lines if x), details=data)
        except Exception as e:  # noqa: BLE001 — a tool reports, it does not crash the turn
            return ToolResult.text(f"comfy_probe failed: {type(e).__name__}: {e}", is_error=True)


def _capture_node_registry(version: str) -> str:
    """Keep this box's node catalogue for its version, once (NodeRegistryCache): every later design
    is checked against it with no box up. '' when there is nothing to say."""
    registry = NodeRegistryCache(Path(current_workspace(".") or "."), _KNOWLEDGE_BASE)
    key = NodeRegistryCache.version_key(version)
    if not key or registry.has_capture(key):
        return ""
    res = _get("/api/object_info", timeout_s=60.0)
    if not res.ok:
        return f"(node list not kept: {_failed(res, 'read the node catalogue')})"
    registry.save(key, res.json() or {})
    return f"node list of ComfyUI {key} kept: designs are checked against it while no GPU is up"


#: What comfy_inventory says when it is called before there is a design to check against.
#: Phrased as a redirection rather than a refusal, because the agent's next move matters more
#: than the error: it should go and research, not go and find another way to list files.
_INVENTORY_TOO_EARLY = (
    "comfy_inventory is not available yet — no workflow has been designed in this conversation.\n"
    "This is deliberate. What is already installed is NOT a design input: this instance is "
    "provisioned for this job and anything missing can be downloaded, so choosing from what "
    "happens to be lying around produces a worse workflow than the one the research supports.\n"
    "Do this instead: pick the model for what the user asked for (kb_lookup) and design it "
    "(pipeline_plan). Inventory unlocks then — and that is when "
    "it is actually useful, for confirming a download landed."
)


class ComfyInventoryTool(Tool):
    name = "comfy_inventory"
    label = "ComfyUI inventory"
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
            settled = ""
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
            offline_note = ""
            if res.ok:
                body = res.json()
            else:
                catalogue, source = _node_list_without_a_machine(res)
                if catalogue is None:
                    return ToolResult.text(_failed(res, node_class), is_error=True)
                body = {node_class: catalogue.get(node_class)} if catalogue.get(node_class) else {}
                offline_note = f"(no machine answered — this is from the {source})\n\n"
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
            # THE SHAPE THE PROMPT MUST USE, not only the schema. Partner nodes carry dynamic
            # inputs whose prompt keys are dotted paths (`model.images.image_1`) — nothing in
            # the raw schema says so, and a graph that guessed `image_1` validated, then died at
            # execute time. node_input_schema flattens ANY node's schema into its real key set.
            schema = node_input_schema.NodeInputSchema(spec)
            example = schema.example("<node id>")
            text = json.dumps(spec, indent=2)[:4000]
            if node_input_schema.deprecated(spec):
                text = (
                    f"DEPRECATED: '{node_class}' has a successor on this instance — "
                    "comfy_node_search names it; a graph with this class fails comfy_validate.\n\n"
                    + text
                )
            text += (
                "\n\nAPI-format `inputs` for this node — copy the KEYS exactly, edit the values; "
                "a link is [node_id, output_index]:\n" + json.dumps(example, indent=1)
            )
            if schema.dynamic:
                text += (
                    "\nDynamic inputs use the dotted keys shown above. The option's own "
                    "sub-inputs go under `<combo>.<name>`, list slots under `<list>.<slot>`; the "
                    "bare sub-name is not an input and the run fails with a TypeError."
                )
            return ToolResult.text(offline_note + text, details=spec)
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(
                f"comfy_node_spec failed: {type(e).__name__}: {e}", is_error=True
            )


#: Loader inputs whose enum is the instance's list of installed WEIGHTS. These are ComfyUI's own
#: input names for its loader nodes — the schema, not a guess about what is installed — which is
#: what lets validate tell "not downloaded yet" from "not a legal value" on an EMPTY box, where
#: the enum holds nothing that looks like a filename (see the shopping-list split in
#: ComfyValidateTool).
_MODEL_FIELDS = ModelReadiness.FIELDS
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
            offline_note = ""
            if res.ok:
                body = res.json() or {}
            else:
                body, source = _node_list_without_a_machine(res)
                if body is None:
                    return ToolResult.text(_failed(res, "node search"), is_error=True)
                offline_note = f"(no machine answered — searched the {source})\n"
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
                    offline_note + f"no node matches '{query}' on this instance. Try a shorter word (a provider "
                    "or model family) — Comfy Cloud runs only the node packs it has preinstalled."
                )
            lines = [offline_note + f"{len(rows)} node(s) match '{query}' (partner nodes first; deprecated last):"]
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


def _execution_error_help(messages) -> str:
    """For an `execution_error`, the failing node's accepted inputs in API format — so the
    repair edits the right key instead of guessing. A TypeError at execute time is almost always
    a key the node does not have (`image_1` for `model.images.image_1`), and the error text alone
    sent the agent through blind retries, a deprecated fallback and a silent model downgrade.
    Fetching the spec here costs one call and ends that."""
    try:
        for m in messages or []:
            if not (isinstance(m, list) and len(m) > 1 and m[0] == "execution_error"):
                continue
            err = m[1] if isinstance(m[1], dict) else {}
            node_type = str(err.get("node_type") or "")
            if not node_type:
                continue
            res = _get(f"/api/object_info/{node_type}")
            spec = (res.json() or {}).get(node_type) if res.ok else None
            if not isinstance(spec, dict):
                continue
            schema = node_input_schema.NodeInputSchema(spec)
            head = (
                f"\n\nNode {err.get('node_id')} is {node_type}"
                + (" (DEPRECATED — use its successor)" if node_input_schema.deprecated(spec) else "")
                + ". Its inputs, in API format — copy the KEYS exactly:\n"
            )
            return head + json.dumps(schema.example("<node id>"), indent=1) + (
                "\nRepair the keys and resubmit the SAME node class and model. A different class "
                "or a smaller model is a design change: back through the ask."
            )
    except Exception:  # noqa: BLE001 — help is optional; the error itself is what matters
        pass
    return ""


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
        entry = _job_entry(prompt_id)
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
                    + json.dumps(messages, indent=2)[:2000]
                    + _execution_error_help(messages),
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


def _job_entry(prompt_id: str) -> dict | None:
    """A finished job as ComfyUI's history entry ({status: {status_str, messages}, outputs}); None
    while it runs. Comfy Cloud answers at /api/jobs/{id} (its /history is deprecated): completed ->
    success; failed / cancelled -> the error it recorded."""
    res = _get(f"/api/jobs/{prompt_id}")
    try:
        job = res.json() if res.ok and res.text.strip() else None
    except ValueError:
        job = None
    if not isinstance(job, dict):
        return None
    status = str(job.get("status") or "")
    if status == "completed":
        return {"status": {"status_str": "success", "messages": []}, "outputs": job.get("outputs") or {}}
    if status in ("failed", "cancelled"):
        error = job.get("execution_error") or {}
        return {"status": {"status_str": status, "messages": [["execution_error", error]] if error else []},
                "outputs": job.get("outputs") or {}}
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

#: THE PERSON'S OWN Comfy key — the only one a ComfyUI they run ever receives. Theirs to set on the
#: Connection section; unset, the partner node answers 401 with the name visible.
_USER_COMFY_KEY_REF = "${USER_COMFY_API_KEY}"


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

    def __init__(self, fed: dict[str, str] | None = None) -> None:
        # role -> workspace-relative file that fills it for THIS run, ahead of the reference slots:
        # an earlier pipeline stage's recorded output. It goes up from where the host downloaded
        # it — a copy into the slot folder is made inside the sandbox, and on a microVM the host,
        # which does the upload, never sees that copy.
        self._fed = dict(fed or {})

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
                    "API-format file (every workflow in this chat has one), or ask the user to export theirs "
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

            # Check live loader names before uploads, billing or submitting /prompt.
            # A past compile-check can predate downloads, deletes or a replaced GPU.
            inventory = _get("/api/object_info", timeout_s=60.0)
            if not inventory.ok:
                return ToolResult.text(_failed(inventory, "check model readiness"), is_error=True)
            readiness = ModelReadiness(inventory.json())
            # Model names as THIS instance spells them (Krea2\x on Windows, Krea2/x on Linux).
            prompt = readiness.respell(prompt)
            missing_models = readiness.missing(prompt)
            if missing_models:
                return ToolResult.text(
                    "Cannot run: required models are not loadable:\n  " + "\n  ".join(missing_models)
                    + "\nUse comfy_validate and comfy_install, then retry. Nothing was submitted.",
                    is_error=True,
                )

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
                fed = {role: rel for role, rel in self._fed.items() if role in roles}
                filled, missing = reference_slots.status(ws, [r for r in roles if r not in fed])
                filled.update(fed)
                if missing:
                    return ToolResult.text(
                        "waiting for reference(s). The user adds them in the References panel, "
                        "and this refuses until every slot is filled:\n"
                        + reference_slots.describe(ws, list(roles))
                        + "\nTell the user in one line which slots are still empty (and ask which "
                        "role a file 'not in any slot' fills, if there is one) and end the turn. "
                        "Nothing announces files as they are added: run again when the user says "
                        "they are there.",
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
            body: dict = {"prompt": prompt}
            # PARTNER NODES: the person's Comfy account pays them, with their key — on Comfy Cloud
            # there is nothing for the platform to price, gate or charge.
            if any(flags.values()):
                body["extra_data"] = {"api_key_comfy_org": _USER_COMFY_KEY_REF}

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


def _clock(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}m{s:02d}s" if m else f"{s}s"


def _loadable_names() -> dict[str, str]:
    """basename -> the name a loader lists it under, for every model file on the instance.
    Manager files a download under a subfolder (`qwen-image-edit/<file>`), and that subfoldered
    name is what the loader's enum carries and what the workflow must say — a graph naming the
    bare file validates as "missing" against an instance that has it."""
    inv = _get("/api/object_info", timeout_s=60.0)
    if not inv.ok:
        raise ValueError("ComfyUI loader inventory unavailable; installation cannot be verified")
    return ModelReadiness(inv.json()).names()


async def _await_loadable(filenames: list[str], abort) -> dict[str, str]:
    """filename -> loader name, for those of `filenames` a loader lists within the rescan grace."""
    started = time.monotonic()
    def base(f: str) -> str:
        return Path(f.replace("\\", "/")).name.lower()

    want = {base(f) for f in filenames}
    found: dict[str, str] = {}
    while True:
        listed = _loadable_names()
        for f in filenames:
            if base(f) in listed:
                found[f] = listed[base(f)]
        if len(found) == len(want) or abort.is_set():
            return found
        if time.monotonic() - started >= _LOADABLE_GRACE_S:
            return found
        await asyncio.sleep(_POLL_S)


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
    label = "Import models into Comfy Cloud"
    # THE WAIT IS DECLARED, so both clocks that could cut it know. The engine's guard times ONE
    # attempt out at _WAIT_ATTEMPT_S and retries into a fresh one, which follows the same import
    # tasks; the sandbox's clock follows the same declaration instead of killing the child at 120 s.
    default_timeout_sec = _WAIT_ATTEMPT_S
    default_retryable = True
    default_retry_on_timeout = True
    default_max_retries = _WAIT_ATTEMPTS
    description = (
        "Import model files Comfy Cloud does not have into the person's Comfy Cloud account. Give "
        "EVERY file comfy_validate listed, in ONE call: filename, its Hugging Face or Civitai "
        "download link (the knowledge base has them; comfy_research finds others) and its kind "
        "(checkpoint, diffusion_model, vae, text_encoder, lora, controlnet, upscale…). Comfy Cloud "
        "downloads them itself; the call returns when ComfyUI lists them, naming the exact loader "
        "name, or with the reason for each one that could not be imported (only Hugging Face and "
        "Civitai links; importing needs a Creator plan or above). Comfy Cloud has 1,300+ models "
        "preinstalled — prefer a design that uses those."
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
                            "description": "The file's Hugging Face /resolve/ link or Civitai "
                            "download link (https://civitai.com/api/download/models/<version id>).",
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

            selected_sources = {f["filename"]: f for f in files}
            installer = ComfyCloudModelImporter(post=_post, get=_get, await_loadable=_await_loadable,
                                                wait_s=_WAIT_ATTEMPT_S - 60)
            installed = await installer.install(files, abort, report)
            export_warning = ""
            repository = WorkflowDependencyRepository(Path(current_workspace(".") or "."))
            for filename, source in selected_sources.items():
                if filename in installed:
                    try:
                        repository.record_model(source, installed[filename])
                    except (OSError, ValueError):
                        export_warning = " Portable installer source metadata could not be recorded for every model."
            return ToolResult.text(
                "Installed and loadable: " + "; ".join(f"{f} -> '{name}'" for f, name in installed.items())
                + ". Re-validate with those names, then run." + export_warning
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"comfy_install failed: {type(e).__name__}: {e}", is_error=True)


async def _no_node_packs(repos: list[str], abort, on_update) -> ToolResult:
    """Comfy Cloud runs only the node packs it has preinstalled — none can be added."""
    return ToolResult.text(
        "Comfy Cloud runs only the node packs it has preinstalled, so these cannot be installed: "
        + ", ".join(repos) + ". The design must use nodes Comfy Cloud has (its node list is what "
        "comfy_validate checks against).", is_error=True)


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
    default_timeout_sec = _WAIT_ATTEMPT_S
    default_retryable = True
    default_retry_on_timeout = True
    default_max_retries = _WAIT_ATTEMPTS
    description = (
        "Check an emitted API-format workflow against THIS instance before anything is "
        "installed or run: every node class must exist, every link must point at a node in the "
        "graph, and every model filename it names must be loadable. The result is pass, or an "
        "itemized report whose missing-file list IS the install shopping list — design first, "
        "validate, install exactly what this names, then run. Pass the workflow's `.api.json` "
        "path."
    )
    parameters = {
        "type": "object",
        "required": ["workflow_path"],
        "properties": {
            "workflow_path": {
                "type": "string",
                "description": "Path to the workflow's .api.json file.",
            },
            "reference_workflow_url": {
                "type": "string",
                "description": "The workflow that proves the model stack: a raw publisher/ComfyUI "
                "workflow JSON URL (GitHub or HF), OR — when the user brought the workflow — the path "
                "of that workflow's .json in their Library (library/uploaded/…, library/saved/…, "
                "library/suggested/…), which is evidence for every file it names. Required for a new "
                "model stack with separate VAE/text encoders; reused for unchanged stacks. Files the "
                "reference does not name are rejected before any install is authorised.",
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
                    f"{path} is a UI-format workflow — validate the .api.json "
                    "beside it (the API file is the one that runs).",
                    is_error=True,
                )

            # Invalidate the old shopping list FIRST. A failed reference fetch/check must not
            # leave a previous validation authorising the wrong companion file.
            studio_state.forget_validated(_workflow_name(path))
            try:
                export_path = Path(path).resolve()
                if export_path.is_relative_to(Path(current_workspace(".") or ".").resolve() / "workflows"):
                    WorkflowInstallerExporter.invalidate(export_path)
            except (OSError, ValueError):
                pass  # do not change validation semantics if old export cleanup is unavailable
            settled = ""
            # THE CATALOGUE: the box's own when there is a box, else the cached one for its
            # version (NodeRegistryCache). A design is checkable before any GPU exists; only the
            # model FILES need the box, and offline they are left to the box.
            res = _get("/api/object_info", timeout_s=60.0)
            live = res.ok
            if not live and not (getattr(res, "error", None) or isinstance(res, _NoInstance)):
                return ToolResult.text(_failed(res, "validate"), is_error=True)
            gpu, vram, version = _machine_info() if live else ("", 0.0, _last_known_version())
            registry = NodeRegistryCache(Path(current_workspace(".") or "."), _KNOWLEDGE_BASE)
            if live:
                try:
                    catalogue = res.json()
                except ValueError:
                    return ToolResult.text(
                        "the instance's node catalogue is too large to fetch whole — validate "
                        "per-node with comfy_node_spec instead.",
                        is_error=True,
                    )
                registry.save(version, catalogue)
                source = "this instance"
            else:
                catalogue, source, _listed = registry.load(version)

            # LAYER 1 — will ComfyUI accept it. A DEPRECATED NODE IN A SERVED DESIGN still runs,
            # and it is not the agent's to swap (fixed_design_shape): there it is a note.
            served = (studio_state.fixed_shapes().get(_workflow_name(path)) or {})
            structure = GraphStructuralValidator(catalogue, live=live).check(graph, served)
            # LAYER 2 — is it right for the models it runs (the knowledge base's rules).
            rules = FamilyRuleValidator(KnowledgeBaseCatalog.shipped(), FamilyStructuralChecks()).check(
                graph, comfyui_version=version, vram_gb=vram, gpu_name=gpu)

            # REFERENCE SLOTS, with their state. Not a compile error either way: an empty slot is
            # the user's move, in the References panel, and comfy_run refuses until it is made.
            structure.bad_enums += reference_slots.bad_roles(graph)
            slot_roles = list(reference_slots.roles_in(graph))
            slots_note = ""
            if slot_roles:
                ws = Path(current_workspace(".") or ".")
                slots_note = (
                    "\nreference slots (the user fills them in the References panel; comfy_run "
                    "refuses while any is EMPTY):\n" + reference_slots.describe(ws, slot_roles)
                )
            # THE REFERENCE CHECK IS A NOTE, NEVER A GATE. It compares the companion files (VAE,
            # text encoders) with a publisher's workflow — a quality hint. It used to refuse the
            # install list until one publisher workflow matched, and a workflow combining two
            # model families (Krea + LTX), or one whose file the user swapped, can never match
            # one: the agent hunted "proof" for an hour and gave up, every time. Downloads stay
            # safe without it — safetensors only, real links only, every file verified.
            stack_note = ""
            if structure.raw_missing:
                reference_problems = WorkflowReferenceRepository(
                    Path(current_workspace(".") or "."), fetch=fetch,
                ).check(graph, str(params.get("reference_workflow_url") or "").strip())
                if reference_problems:
                    stack_note = (
                        "\nnote — companion files not confirmed by a publisher workflow (a hint, not "
                        "a blocker; install and run as usual): " + " | ".join(reference_problems)
                    )
            # THE RECORD THE INSTALL GATES READ — only from a LIVE check: comfy_install answers
            # from this and nothing else, and a cached catalogue knows nothing of the files.
            if live:
                try:
                    studio_state.mark_validated(_workflow_name(path), structure.raw_missing, structure.raw_unknown)
                except Exception:  # noqa: BLE001 — the report still goes out
                    pass
            if structure.deprecated_notes:
                stack_note += (
                    "\nnote — deprecated but still runs, kept because this design was given as it "
                    "is: " + ", ".join(structure.deprecated_notes)
                )
            fixed = studio_state.fixed_design()
            knowledge = _rule_report_text(rules, fixed_design=fixed)
            checked_against = (
                "" if live else
                f"\nchecked WITHOUT a GPU, against the {source}. Model files are not checked here: "
                "the box checks them when it is up (validate again then, before installing)."
            )
            blocking_rules = [] if fixed else rules.errors
            if structure.compiles and not blocking_rules:
                artifacts, export_note = [], ""
                if live:
                    try:
                        artifacts, manifest = WorkflowInstallerExporter(
                            Path(current_workspace(".") or "."), get=_get,
                            known_files=KnowledgeBaseCatalog.shipped().model_sources(),
                        ).export(path, graph, catalogue)
                        export_note = "\nPortable installer: " + artifacts[0] + "\nDependency manifest: " + artifacts[1]
                        if manifest["unresolved"]:
                            export_note += "\nInstaller INCOMPLETE (will refuse to install): " + "; ".join(manifest["unresolved"])
                        else:
                            export_note += "\nUse the user's ComfyUI Python with --comfy-dir PATH; --dry-run previews it."
                    except Exception as error:  # export is optional; never turn a valid graph into a failure
                        export_note = f"\nPortable installer export unavailable ({type(error).__name__}); validation still passed."
                head = (
                    f"compiles: all {len(graph)} node(s) exist on this instance, links resolve, "
                    "and every model file it names is loadable. Safe to comfy_run."
                    if live else
                    f"the design holds: all {len(graph)} node(s), links, inputs and values check out "
                    "against the node list, and the knowledge base finds nothing wrong with it."
                )
                return ToolResult.text(
                    head
                    + (f"\n{settled}" if settled else "")
                    + checked_against + knowledge + slots_note + export_note + stack_note,
                    artifacts=artifacts,
                )
            lines = [
                "the workflow does NOT compile against this instance:" if live else
                "the design does NOT hold (checked without a GPU):"
            ]
            if structure.unknown_nodes:
                lines.append(
                    "unknown node classes — USUALLY A WRONG NAME, not a missing pack. Look each "
                    "one up (comfy_node_spec / comfy_inventory) and re-emit with the real class. "
                    "If the class truly belongs to a pack Comfy Cloud lacks, the design must "
                    "use other nodes — Comfy Cloud runs only its preinstalled packs:"
                )
                lines += [f"  {x}" for x in structure.unknown_nodes]
            if structure.bad_links:
                lines.append("broken links:")
                lines += [f"  {x}" for x in structure.bad_links]
            if structure.bad_inputs:
                lines.append(
                    "inputs the node does not accept, or needs (comfy_node_spec <class> prints "
                    "the exact API-format keys — copy them):"
                )
                lines += [f"  {x}" for x in structure.bad_inputs]
            if structure.bad_enums:
                lines.append("invalid values (fix the workflow):")
                lines += [f"  {x}" for x in structure.bad_enums]
            if structure.bad_values:
                lines.append("values outside what the node accepts (fix the workflow):")
                lines += [f"  {x}" for x in structure.bad_values]
            if structure.no_output_node:
                lines.append(
                    "no output node: ComfyUI refuses a graph that saves nothing ('Prompt has no "
                    "outputs'). End it with SaveImage / SaveVideo."
                )
            if structure.missing_files:
                lines.append("model files to install (this is the comfy_install shopping list):")
                lines += [f"  {x}" for x in structure.missing_files]
                # THE NEXT CALL, NAMED. Left to improvise, the agent once "checked" a missing
                # file by fetching its weights URL as a page, and froze the daemon doing it.
                lines.append(
                    "next: comfy_install these files, each with its direct download link (a Hugging Face "
                    "/resolve/ or Civitai download URL found by a comfy_research SEARCH). Never fetch "
                    "the file itself; the GPU downloads it."
                )
            if settled:
                lines.append(settled)
            if checked_against:
                lines.append(checked_against.strip())
            if knowledge:
                lines.append(knowledge.strip())
            if slots_note:
                lines.append(slots_note.strip())
            if stack_note:
                lines.append(stack_note.strip())
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
        "available. Give it a `model` from that list (with `seconds` for video and `resolution`) "
        "to get the exact price of that model for THIS job — the numbers every paid ask_user row "
        "carries, copied as printed. Give it a workflow name to price that emitted graph exactly. "
        "The user is charged these credits when the run is submitted; nobody has to supply an "
        "API key."
    )
    parameters = {
        "type": "object",
        "properties": {
            "workflow": {
                "type": "string",
                "description": (
                    "A workflow to price exactly: its path, or just"
                    " its name. Omit to list the whole catalogue."
                ),
            },
            "model": {
                "type": "string",
                "description": "A model id exactly as the catalogue lists it, e.g. 'seedance-2-5'.",
            },
            "seconds": {
                "type": "number",
                "description": "Video length for a per-second model. Omitted: the provider's default length.",
            },
            "resolution": {
                "type": "string",
                "description": "The tier as the catalogue lists it in brackets, e.g. '720p', '2k', 'high'.",
            },
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            import partner_pricing

            table = partner_pricing.load_table()
            rate = _platform_rate()
            model = str(params.get("model") or "").strip()
            if model:
                return self._model_quote(table, rate, model, params)
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
    def _model_quote(table: dict, rate: float, model: str, params: dict) -> ToolResult:
        """One model's price for this job: the exact row an approval card shows."""
        import partner_pricing

        seconds = params.get("seconds")
        seconds = float(seconds) if isinstance(seconds, (int, float)) and seconds > 0 else None
        resolution = str(params.get("resolution") or "").strip()
        quote = partner_pricing.price_model(model, seconds, resolution, table)
        if not quote.ok:
            return ToolResult.text(
                f"no model {model!r} in the price list — call comfy_price with no arguments and "
                "use an id exactly as it is listed.",
                is_error=True,
            )
        item = quote.items[0]
        credits = quote.platform_credits(rate)
        length = f", {item.quantity:g} s" if item.unit == "per_second" else ""
        tier = f", {item.note}" if item.note else ""
        return ToolResult.text(
            f"{model}{tier}{length}: usd={quote.usd:.4f} credits={credits:,} — put exactly these "
            "numbers on this model's ask_user row.",
            details={"usd": round(quote.usd, 4), "credits": credits, "credits_per_usd": rate},
        )

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


def _export_design_installer(rel: str) -> str:
    """ONE installer for a pipeline's combined workflow, from Comfy Cloud's node list. Not through
    comfy_validate: the stages were each checked against their own model family's rules, and in
    the joined graph one family's rules land on another's nodes (H3's "cfg must be 1.0" on the
    Qwen stage's sampler) — false alarms that had the agent "fixing" a correct design."""
    root = Path(current_workspace(".") or ".")
    path = root / rel
    graph = json.loads(path.read_text(encoding="utf-8"))
    info = _get("/api/object_info", timeout_s=60.0)
    if not info.ok:
        raise ValueError(_failed(info, "read Comfy Cloud's node list for the installer"))
    artifacts, manifest = WorkflowInstallerExporter(
        root, get=_get, known_files=KnowledgeBaseCatalog.shipped().model_sources(),
    ).export(path, graph, info.json())
    note = f"installer {artifacts[0]}"
    if manifest["unresolved"]:
        note += " — INCOMPLETE (it will refuse to install): " + "; ".join(manifest["unresolved"])
    return note


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


def _machine_info() -> tuple[str, float, str]:
    """(GPU name, VRAM in GB, ComfyUI version) of the connected machine — empty when it does not say."""
    res = _get("/api/system_stats", timeout_s=15.0)
    data = (res.json() or {}) if res.ok else {}
    devices = data.get("devices") or []
    first = devices[0] if devices and isinstance(devices[0], dict) else {}
    version = str((data.get("system") or {}).get("comfyui_version") or "")
    return str(first.get("name") or ""), int(first.get("vram_total") or 0) / (1024 ** 3), version


def _node_list_without_a_machine(res) -> tuple[dict | None, str]:
    """(object_info, where it came from) when NO machine answered — the node list captured from the
    user's box, else the one shipped with the agent — so designing never waits on a GPU. None when a
    machine DID answer with an error: that error is the answer, and it is not papered over."""
    if not (getattr(res, "error", None) or isinstance(res, _NoInstance)):
        return None, ""
    catalogue, source, _listed = NodeRegistryCache(Path(current_workspace(".") or "."), _KNOWLEDGE_BASE).load(
        _last_known_version())
    return catalogue, source


def _last_known_version() -> str:
    """The ComfyUI version the last probe saw ('' when nothing was ever probed) — what a design is
    checked against while no box is up."""
    return str((studio_state.read().get("instance") or {}).get("version") or "")


def _rule_report_text(report: RuleReport, fixed_design: bool = False) -> str:
    """The knowledge base's verdict, for a tool result: errors block, warnings must be answered.
    A design given AS IT IS (a template, a Library workflow) is not the agent's to change: there
    the same findings are notes to mention, not orders."""
    if not report.families:
        return ""
    lines = [f"\nknowledge base ({', '.join(report.families)}): {report.checked} rule(s) checked"]
    if fixed_design and report.findings:
        lines.append("this design was given as it is — do not change it; where these matter, tell the user in one line:")
        lines += [f"  {f.render()}" for f in report.findings]
    elif report.errors or report.warnings:
        if report.errors:
            lines.append("WRONG FOR THESE MODELS — fix each:")
            lines += [f"  {f.render()}" for f in report.errors]
        if report.warnings:
            lines.append("ANSWER EACH — fix it, or say in one line why it is right for this job:")
            lines += [f"  {f.render()}" for f in report.warnings]
    if report.not_judged:
        lines.append(f"not judged from the graph alone (values computed at run time): {len(set(report.not_judged))}")
    for family, guide in report.prompt_guides.items():
        lines.append(f"\n{family.upper()} PROMPT FORMAT — when you write or change the prompt, use this "
                     f"(a template or Library workflow run AS IT IS keeps its prompt):\n{guide}")
    return "\n".join(lines)


def _machine_nodes() -> dict:
    """The machine's /api/object_info — every node class it has loaded, with its inputs."""
    res = _get("/api/object_info", timeout_s=60.0)
    if not res.ok:
        raise ValueError(_failed(res, "read the machine's nodes"))
    return res.json() or {}


def register(api, ctx):
    # Imported by bare name: the loader puts this plugin's folder on sys.path, so siblings are
    # top-level modules here rather than a package.
    from comfy_emit import ComfyEmitTool
    from comfy_research import ComfyResearchTool
    from comfy_delete import ComfyDeleteTool
    from library_find_tool import LibraryFindTool
    from library_read_tool import LibraryReadTool
    from library_use_tool import LibraryUseTool
    from template_use_tool import TemplateUseTool
    from template_setup_tool import TemplateSetupTool
    from template_setup_guide_tool import TemplateSetupGuideTool
    from template_about_draft_tool import TemplateAboutDraftTool

    api.register_tool(ComfyInstallTool())
    api.register_tool(ComfyProbeTool())
    api.register_tool(ComfyEmitTool(node_list=lambda: NodeRegistryCache(
        Path(current_workspace(".") or "."), _KNOWLEDGE_BASE).load(_last_known_version())[0]))
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
    # THE LIBRARY — the one folder chats share, read-only for the agent. See library_paths.
    api.register_tool(LibraryFindTool())
    api.register_tool(LibraryReadTool())
    api.register_tool(LibraryUseTool(
        converter=lambda: EditorGraphConverter(get=_get, post=_post) if _override() else None,
    ))
    api.register_tool(TemplateUseTool())
    # A TEMPLATE'S MACHINE SETUP drives the same two install tools the agent would, from the
    # links the template's setup guide carries — see template_setup_tool.
    api.register_tool(TemplateSetupTool(
        object_info=_machine_nodes,
        install_packs=_no_node_packs,
        install_models=lambda files, abort, on_update: ComfyInstallTool().execute(
            "", {"files": files}, abort, on_update),
    ))
    api.register_tool(TemplateSetupGuideTool())
    api.register_tool(TemplateAboutDraftTool(ctx.config))
    # PHASE 1 — THE DESIGN, WITH NO GPU: the knowledge base, the pipeline of stages, and the checks.
    # Registered here; an agent sees them only when its agent.toml [tools] lists them.
    from kb_lookup_tool import KbLookupTool
    from pipeline_plan_tool import PipelinePlanTool
    from pipeline_present_tool import PipelinePresentTool
    from pipeline_provision_tool import PipelineProvisionTool
    from pipeline_run_tool import PipelineRunTool
    from pipeline_status_tool import PipelineStatusTool
    from pipeline_validate_tool import PipelineValidateTool
    from stage_bind_tool import StageBindTool
    from stage_edit_graph_tool import StageEditGraphTool
    from stage_set_tool import StageSetTool

    api.register_tool(KbLookupTool())
    api.register_tool(PipelinePlanTool())
    api.register_tool(StageSetTool())
    api.register_tool(StageBindTool())
    api.register_tool(StageEditGraphTool())
    api.register_tool(PipelineValidateTool())
    api.register_tool(PipelinePresentTool())
    # PHASES 2 AND 3 drive the same tools the agent used by hand — validate, install, run, collect,
    # download — so their gates (nothing installed or run before the card is answered; only files a
    # live validation listed are installed) hold for a pipeline exactly as for one workflow.
    api.register_tool(PipelineProvisionTool(
        validate=lambda path, abort, upd: ComfyValidateTool().execute(
            "", {"workflow_path": path}, abort or asyncio.Event(), upd),
        install_models=lambda files, abort, upd: ComfyInstallTool().execute("", {"files": files}, abort, upd),
        export_installer=_export_design_installer,
        timeout_s=_WAIT_ATTEMPT_S, max_retries=_WAIT_ATTEMPTS,
    ))
    api.register_tool(PipelineRunTool(
        run=lambda path, fed, abort, upd: ComfyRunTool(fed).execute(
            "", {"workflow_path": path, "timeout_s": _RUN_WAIT_CAP_S}, abort, upd),
        run_status=lambda prompt_id, abort, upd: ComfyRunStatusTool().execute(
            "", {"prompt_id": prompt_id, "timeout_s": _RUN_WAIT_CAP_S}, abort, upd),
        download=lambda files, abort, upd: ComfyDownloadTool().execute("", {"files": files}, abort, upd),
    ))
    api.register_tool(PipelineStatusTool())

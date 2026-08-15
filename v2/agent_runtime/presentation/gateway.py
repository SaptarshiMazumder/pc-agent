"""WebSocket gateway: receives chat.send, runs the agent, broadcasts events.

Mirrors the reference gateway's chat.send semantics:
- respond immediately with {runId}; the run executes async
- one active run per session (busy -> error response)
- idempotencyKey dedupe
- loop events broadcast to all connected clients as chat.event frames
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import websockets
from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.http11 import Response as HttpResponse

from agent_runtime import __version__, lifecycle
from agent_runtime.application.run_context import (
    current_run_context,
    set_trace_ids,
    take_run_outcome,
)
from agent_runtime.application.services.agent_service import AgentService
from agent_runtime.config import Config, client_accounts_url
from agent_runtime.domain.agent import (
    RunMode,
    agent_id_from_session_key,
    cron_session_key,
    setting_env_name,
)
from agent_runtime.domain.autonomy import ScheduledTask, resolve_run_outcome
from agent_runtime.domain.events import AgentEvent
from agent_runtime.domain.messages import Artifact, artifact_to_dict
from agent_runtime.domain import ownership
from agent_runtime.domain.notify import Notification
from agent_runtime.infrastructure import accounts, telemetry, user_state
from agent_runtime.infrastructure.env_file import EnvFile
from agent_runtime.infrastructure.files import guess_mime, is_under_roots, save_upload
from agent_runtime.infrastructure.llm import model_proxy
from agent_runtime.infrastructure.memory.local_store import list_sessions
from agent_runtime.presentation.protocol import (
    Event,
    ProtocolError,
    Request,
    Response,
    dump_frame,
    parse_frame,
)

log = logging.getLogger("agentd")

# Uploads (attachments) ride the authenticated WS channel as a single base64 frame, so
# the server's inbound frame cap must clear a base64-inflated file. 48 MiB of frame ≈ a
# 34 MiB raw attachment (see UPLOAD_MAX_BYTES); larger files should chunk (future work).
MAX_WS_FRAME = 48 * 1024 * 1024
UPLOAD_MAX_BYTES = 32 * 1024 * 1024

# The wire-protocol generation this daemon speaks (see docs/PROTOCOL.md). Additive changes
# (new methods, new payload fields) do NOT bump this; only a breaking change to an existing
# frame/method would. Clients send their own number in `hello {protocol}` and get back
# `compatible` — v1 semantics are advisory (advertise, don't reject), so old clients keep
# working against newer daemons.
PROTOCOL_VERSION = 1

# The STABLE method tier (docs/PROTOCOL.md §4): everything an agent-app (a connection scoped
# with `scope=agent:<id>`) may call. Everything else — config, installs, projects, automation —
# is the HOST tier, denied on scoped connections. Apps INVOKE the backend; they never extend
# or administer it.
APP_SCOPED_METHODS = frozenset(
    {
        "hello",
        "chat.send",
        "chat.abort",
        "sessions.list",
        "sessions.history",
        "sessions.rename",
        "sessions.delete",
        "agents.list",
        "agents.detail",
        "tools.list",
        "tools.invoke",
        "capabilities.list",
        "plugins.catalog",
        "workspace.list",
        "workspace.mkdir",
        "workspace.upload",
        "workspace.delete",
        "notifications.list",
        "notifications.ack",
        # An APP-AGENT product (its own exe/window) is a first-party desktop client: it signs
        # the user in so the LOCAL daemon runs on platform keys. Safe for a scoped app because
        # (a) these are absent from PUBLIC_APP_METHODS, so tokenless/cloud visitors are refused
        # at the public gate, and (b) the handlers no-op on a hosted (accounts-mode) daemon —
        # sign-in only manages the LOCAL model-key credential. Not administration of the backend.
        "platform.connect",
        "platform.disconnect",
        "platform.status",
        "platform.setModelProxyUrl",
        # Deprecated compatibility method for pre-rename desktop clients.
        "platform.setGatewayUrl",
        # SETTINGS. An agent app renders its own settings page — above all BYOK: the user
        # pastes their provider key and the agent works. That is configuration OF the local
        # daemon by its own user, not administration of the backend, so it belongs here.
        # config.get REDACTS its secret-bearing fields for an INSTALLED agent (see
        # _config_get): a page that ships inside a downloaded package must never be able to
        # read the key back out and post it somewhere — there is no CSP on /apps/ pages.
        # config.set needs no such split; its allowlist already ignores everything outside
        # EXPOSED_CONFIG_KEYS.
        "config.get",
        "config.set",
        # DECLARED MCP. `status` answers "why does this agent have no tools" on its own settings
        # page; `approve` is the user consenting to launch the command their agent declared. Both
        # are forced onto the connection's own agentId like every other scoped write, so a page
        # can only ever see and approve ITS OWN servers. Neither can invent a server: approve
        # refuses any name the agent's own agent.toml does not declare.
        "mcp.status",
        "mcp.approve",
        # OAUTH. Signing in to the third-party service THIS agent declared, from the page the
        # user is already looking at. Forced onto the connection's own agentId like every other
        # scoped call, and each handler refuses a name the agent's own agent.toml does not
        # declare — so a page can neither see nor start anybody else's sign-in.
        "oauth.connect",
        "oauth.status",
        "oauth.disconnect",
    }
)

# CROSS-AGENT READS — the ONE exception to "an app can never act as another agent".
#
# Every scoped request normally has its `agentId` OVERWRITTEN with the connection's own agent
# (see _handle_request), which is what stops a product window — weather's dashboard, a
# downloaded agent's UI — from reading anything else on the machine. Agent Builder is the one
# window whose entire job is looking at OTHER agents: it has to show you the agent it just
# built. So it, and only it, may name a different agent on these methods.
#
# HARDCODED ON PURPOSE. Not config, not env, not agent.toml — there is no input an agent could
# write to add itself here. Changing this list means changing this file.
#
# READS ONLY. chat.send, sessions.delete, workspace.delete/upload/mkdir are deliberately
# absent: reading about another agent is the feature, acting or destroying as one is not.
CROSS_AGENT_READS: dict[str, frozenset[str]] = {
    "agent-builder": frozenset(
        {
            "agents.detail",
            "sessions.list",
            "sessions.history",
            "workspace.list",
            "tools.list",
            "capabilities.list",
        }
    ),
}

# The PUBLIC tier (hosted deployments): what an UNAUTHENTICATED connection scoped to an
# agent whose [app] declares `public = true` may call. A strict subset of the scoped tier —
# deliberately excludes chat.* (burns LLM tokens), sessions.* and workspace.* (state): a
# public visitor can render the app and invoke the author-declared `public_tools`, nothing
# else. Private/local daemons never see this tier (auth passes or the conn is refused).
PUBLIC_APP_METHODS = frozenset(
    {
        "hello",
        "agents.list",
        "agents.detail",
        "tools.list",
        "tools.invoke",
    }
)
# Abuse guards for the public tier — coarse by design (real rate limiting belongs to the
# CDN/WAF in front of a hosted daemon, not here).
MAX_PUBLIC_CONNECTIONS = 256  # FD-exhaustion guard
PUBLIC_INVOKE_CONCURRENCY = 8  # global in-flight cap for public tools.invoke

# Hosted web delivery (`GET /apps/<id>` for an agent this daemon has not installed): the store
# printed `<web host>/apps/<id>/` on every web-delivered bundle's card, so the FIRST VISITOR is
# the install trigger. Both throttles bound what a stranger's URL bar can make the daemon do:
WEB_SYNC_RETRY_SECONDS = 60  # a failed visitor-triggered sync answers with its error this long
WEB_SYNC_REFRESH_SECONDS = 300  # how often an entry-page open re-checks the registry for updates


def _scoped_event_allowed(name: str, payload: dict, agent_id: str) -> bool:
    """What an agent-scoped app connection may receive (docs/PROTOCOL.md §7): its OWN agent's
    run/session events, roster changes, and its notifications. Everything else — other agents'
    runs, marketplace progress, project admin — stays host-only. Pure policy, unit-testable."""
    if name in ("chat.event", "sessions.changed"):
        return payload.get("agentId") == agent_id
    if name == "agents.changed":
        return True
    if name == "notification":
        return payload.get("agentId") in (agent_id, "", None)
    return False


def _effective_model(config) -> str:
    """The reasoning model as the models layer resolves it (CONFIG-ONLY) — for display/status. Shows
    "(CONFIG MISSING)" instead of crashing when no agentd.config.json was loaded. When cost-efficiency
    routing is ON, the static brain id alone is MISLEADING (text turns actually run the cheap text_model,
    only image turns use the vision_model), so reflect the routing here — this is the banner the user
    reads to know which model is really doing the work."""
    from agent_runtime.application.tool_models import ConfigMissingError, brain_model

    try:
        base = brain_model(config)
    except ConfigMissingError:
        return "(CONFIG MISSING)"
    ce = getattr(config, "cost_efficiency", None) or {}
    if (
        isinstance(ce, dict)
        and ce.get("enabled")
        and (ce.get("text_model") or ce.get("vision_model"))
    ):
        text = ce.get("text_model") or base
        vision = ce.get("vision_model") or base
        if text != vision:
            return f"{text} -> {vision} on images (cost-efficiency)"
        return f"{text} (cost-efficiency)"
    return base


# sessions.history DISPLAY caps: a full transcript re-sends every tool dump + inline
# base64 image, which can be MEGABYTES (blows the WS message limit, and the client
# renders none of the image bytes anyway). We trim to a display-shaped transcript —
# generous text, bounded tool output, image DATA dropped (kept as a marker). The live
# view already shows a first-line preview + expandable; history matches that intent.
_HISTORY_TOOL_RESULT_CAP = 4000
_HISTORY_THINKING_CAP = 8000
_HISTORY_TEXT_CAP = 40000
_HISTORY_ARG_CAP = 2000


def _cap(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + f"\n…[+{len(text) - limit} chars]"


def _trim_history_block(block: dict) -> dict:
    """Trim ONE content block for display. Image bytes are dropped (marker kept); text/
    thinking are capped; tool-call args with huge string values are capped."""
    kind = block.get("type")
    if kind == "text":
        return {"type": "text", "text": _cap(block.get("text", ""), _HISTORY_TEXT_CAP)}
    if kind == "thinking":
        return {
            "type": "thinking",
            "thinking": _cap(block.get("thinking", ""), _HISTORY_THINKING_CAP),
        }
    if kind == "image":
        return {"type": "image", "data": "", "mimeType": block.get("mimeType", ""), "elided": True}
    if kind == "toolCall":
        args = block.get("arguments") or {}
        trimmed = {
            k: (_cap(v, _HISTORY_ARG_CAP) if isinstance(v, str) else v) for k, v in args.items()
        }
        return {
            "type": "toolCall",
            "id": block.get("id", ""),
            "name": block.get("name", ""),
            "arguments": trimmed,
        }
    return block


def _trim_history_message(message: dict) -> dict:
    """Trim one wire-form message for the sessions.history payload (keeps it KB, not MB)."""
    role = message.get("role")
    ts = message.get("ts", "")  # the line's stored send time (ISO) — kept for display
    if role == "user":
        out = {
            "role": "user",
            "content": _cap(message.get("content", ""), _HISTORY_TEXT_CAP),
            "ts": ts,
            "timestamp": message.get("timestamp", 0),
        }
        if message.get(
            "attachments"
        ):  # user-supplied files (by ref) — so a resumed chat shows them
            out["attachments"] = message["attachments"]
        return out
    if role == "assistant":
        return {
            "role": "assistant",
            "content": [_trim_history_block(b) for b in message.get("content") or []],
            "stopReason": message.get("stopReason", "stop"),
            "errorMessage": message.get("errorMessage"),
            "ts": ts,
            "timestamp": message.get("timestamp", 0),
        }
    if role == "toolResult":
        trimmed = {
            "role": "toolResult",
            "toolCallId": message.get("toolCallId", ""),
            "toolName": message.get("toolName", ""),
            "content": [
                {
                    "type": "text",
                    "text": _cap(
                        "".join(
                            b.get("text", "")
                            for b in message.get("content") or []
                            if b.get("type") == "text"
                        ),
                        _HISTORY_TOOL_RESULT_CAP,
                    ),
                }
            ],
            "isError": message.get("isError", False),
            "ts": ts,
            "timestamp": message.get("timestamp", 0),
        }
        if message.get("artifacts"):  # keep declared deliverables so a resumed chat renders them
            trimmed["artifacts"] = message["artifacts"]
        return trimmed
    return message


# The prompt posted on an autonomous heartbeat tick (no user message). The agent's
# HEARTBEAT.md checklist is assembled into the system prompt for heartbeat runs.
HEARTBEAT_PROMPT = (
    "This is an autonomous heartbeat tick — no user is present. Read your HEARTBEAT.md "
    "checklist (above) and act on anything that needs attention using your tools. When "
    "done, call heartbeat_respond exactly once with the outcome; if nothing needed "
    "attention, use outcome='nothing-to-do' and notify=false."
)

# A scheduled `deliver=message` task: the agent outputs the stored text verbatim
# (reuses the normal run/stream path — no separate delivery plumbing).
OUTBOX_PROMPT = (
    "Deliver the following message to the user verbatim — output it exactly as written, "
    "with no preamble, summary, or additions:\n\n{text}"
)


@dataclass
class RunHandle:
    run_id: str
    session_key: str
    abort: asyncio.Event
    client_id: str | None = None  # the client connection that started this run
    parent_session_key: str | None = None  # set for a SUB-AGENT run -> its progress is
    #                                         relayed (compactly) to the parent's view
    parent_run_id: str | None = None  # SUB-AGENT: the run that spawned this one. Without it a
    #                                   delegated run's cost looks like it came from nowhere.
    trigger: str = "chat"  # chat | cron | heartbeat | channel | webhook | app | subagent.
    #                        Most runs do NOT start at a chat box, and unattended ones (cron)
    #                        carry the highest cost risk — so they need their own dimension.
    task: asyncio.Task | None = None
    cron_run_id: str | None = None  # set for cron runs -> recorded in the run history
    cron_task_id: str | None = None  # the cron job's id (for failure-alert escalation, S14)
    cron_failure_alert: int = 0  # auto-pause + alert after N consecutive failures (0=off)


def subagent_relay(child_session_key: str, event: AgentEvent) -> AgentEvent | None:
    """Compact ONE child-run event into a single `subagent_event` for the PARENT's view —
    only the meaningful beats: start, each tool the child runs, and done/error. Raw text /
    thinking deltas are dropped (relaying them would flood the parent, especially with several
    children running at once). Returns None to skip. The client renders these dimmed/indented
    so a parent run shows its sub-agents working instead of going silent."""
    child = agent_id_from_session_key(child_session_key)
    if event.type == "agent_start":
        return AgentEvent("subagent_event", {"childAgent": child, "kind": "start"})
    if event.type == "tool_execution_start":
        return AgentEvent(
            "subagent_event",
            {"childAgent": child, "kind": "tool", "tool": event.payload.get("toolName", "")},
        )
    if event.type == "agent_end":
        err = event.payload.get("error")
        return AgentEvent(
            "subagent_event",
            {
                "childAgent": child,
                "kind": "error" if err else "done",
                "detail": err or event.payload.get("stopReason", ""),
            },
        )
    return None


def _subagent_depth(session_key: str) -> int:
    """How many sub-agent levels deep a session is. 0 = a top-level run. Child keys encode the
    level as ``agent:<id>:sub:<depth>:<hex>``; an older flat ``...:sub:<hex>`` counts as 1.
    cron/channel keys (no ``sub`` segment) are depth 0."""
    parts = session_key.split(":")
    if "sub" not in parts:
        return 0
    nxt = parts[parts.index("sub") + 1] if parts.index("sub") + 1 < len(parts) else ""
    return int(nxt) if nxt.isdigit() else 1


def _oauth_page(message: str) -> HttpResponse:
    """The one page a user sees at the end of a sign-in. Deliberately plain: it exists to say
    which of the two things happened and that they may close the tab."""
    body = (
        "<!doctype html><meta charset=utf-8><title>agentd</title>"
        "<body style=\"font-family:system-ui,sans-serif;display:grid;place-items:center;"
        "min-height:100vh;margin:0;background:#fafaf7;color:#1a1a1a\">"
        f"<main style=\"max-width:32rem;padding:2rem;line-height:1.5\"><p>{message}</p></main>"
    ).encode("utf-8")
    return HttpResponse(
        200,
        "OK",
        Headers({"Content-Type": "text/html; charset=utf-8", "Content-Length": str(len(body))}),
        body,
    )


def _guarded_with_source(tools, config) -> list:
    """Wrap tools in GuardedTool AND stamp their catalog ``source`` (mcp:<server> for the
    namespaced MCP tools that flow through here). Mirrors the container's wrap step."""
    from agent_runtime.application.services.agent_service import tool_source
    from agent_runtime.infrastructure.tools.guard import GuardedTool, resolve_policy

    out = []
    for t in tools:
        gt = GuardedTool(t, resolve_policy(config, t))
        gt.source = tool_source(t)
        out.append(gt)
    return out


def _server_dict(s) -> dict:
    """Serialize an McpServerConfig back to a JSON-config dict (omit empty fields)."""
    out: dict = {"name": getattr(s, "name", "")}
    for k in ("transport", "command", "env", "url", "headers"):
        v = getattr(s, k, None)
        if v:
            out[k] = v
    return out


def _persist_mcp_servers(config) -> bool:
    """Write ``config.mcp_servers`` back to agentd.config.json (so a hot-add survives restart).
    Preserves every other key in the file. Best-effort: a write failure is logged, not fatal."""
    import json
    import os
    from pathlib import Path

    from agent_runtime.config import V2_ROOT

    path = None
    for cand in (
        os.environ.get("AGENTD_CONFIG"),
        "agentd.config.json",
        str(V2_ROOT / "agentd.config.json"),
    ):
        if cand and Path(cand).is_file():
            path = Path(cand)
            break
    if path is None:
        path = V2_ROOT / "agentd.config.json"  # create at the default location
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        data["mcp_servers"] = [_server_dict(s) for s in (config.mcp_servers or [])]
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return True
    except Exception as e:  # noqa: BLE001 — persistence is best-effort
        log.warning("could not persist mcp_servers to %s: %s", path, e)
        return False


def _persist_webhooks(config) -> bool:
    """Write ``config.webhooks`` back to agentd.config.json (so a created hook survives restart).
    Preserves every other key. Best-effort: a write failure is logged, not fatal."""
    import json
    import os
    from pathlib import Path

    from agent_runtime.config import V2_ROOT

    path = None
    for cand in (
        os.environ.get("AGENTD_CONFIG"),
        "agentd.config.json",
        str(V2_ROOT / "agentd.config.json"),
    ):
        if cand and Path(cand).is_file():
            path = Path(cand)
            break
    if path is None:
        path = V2_ROOT / "agentd.config.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        data["webhooks"] = list(config.webhooks or [])
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return True
    except Exception as e:  # noqa: BLE001 — persistence is best-effort
        log.warning("could not persist webhooks to %s: %s", path, e)
        return False


# --- editable-config surface (config.get / config.set) ------------------------------
# Provider API keys the settings UI can inspect (presence only) and set. These are
# SECRETS: they live in the .env file (read by LiteLLM/tools straight from os.environ),
# never in agentd.config.json. config.get returns per-key presence booleans (`env`) AND the
# actual values (`envValues`) so the LOCAL settings UI can show a saved key masked with a
# reveal (eye) toggle. Deliberate local-first choice: the gateway is loopback-only + token-
# authed, so a key never leaves the user's own machine; every client reads the SAME .env.
PROVIDER_ENV_KEYS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "OPENROUTER_API_KEY",
    "DEEPSEEK_API_KEY",
    "GROQ_API_KEY",
    "MISTRAL_API_KEY",
    "XAI_API_KEY",
    "TOGETHER_API_KEY",
    "FIREWORKS_API_KEY",
    "FAL_KEY",
    "REPLICATE_API_TOKEN",
    "BRAVE_API_KEY",
    "PARALLEL_API_KEY",
)
# The config knobs the settings UI may READ and WRITE (a curated allowlist — never the
# install identity `distribution`, the `gateway_token` secret, or `config_path`). Anything
# not here is ignored on write, so a client can't set an arbitrary attribute.
EXPOSED_CONFIG_KEYS = (
    "agent_name",
    "model",
    "reasoning_effort",
    "max_turns",
    "model_fallbacks",
    "cost_efficiency",
    "model_catalog",
    "model_defaults",
    "llm_idle_timeout_seconds",
    "llm_request_timeout_seconds",
    "host",
    "port",
    "workspace",
    "state_dir",
    "tool_timeout_default",
    "tool_retries_default",
    "tool_loop_max_repeats_default",
    "tool_loop_warn_after_errors_default",
    "tools_enabled",
    "tools_disabled",
    "verify_tool",
    "completeness_check",
    "computer_enabled",
    "execution_contract",
    "subagents_enabled",
    "subagent_max",
    "subagent_max_depth",
    "memory_enabled",
    "memory_auto_recall",
    "memory_auto_recall_limit",
    "skill_workshop",
    "mcp_workshop",
    "agent_messaging_enabled",
    "autonomy_enabled",
    "heartbeat_default_interval",
    "heartbeat_active_hours",
    "notify_enabled",
    "safe_to_send_check",
    "workspace_index_enabled",
    "resource_manager_enabled",
    "resource_vision_enabled",
    "resource_summarize_enabled",
    "scratch_ttl_hours",
    "context_max_messages",
    "event_log_enabled",
    "diagnostics_upload",
    "parallel_search_enabled",
    "google_account",
    "public_url",
    "webhook_host",
    "webhook_port",
    "skills_relevance_enabled",
    "plugins",
    "app_hosts",
    # Per-agent overrides, {agent_id: {override_default, model, ...}}. Exposing it is the WHOLE
    # backend change for per-agent settings: _config_get returns every key in this tuple and
    # _config_set merges + hot-applies every writable one, so a settings page can read and
    # write the agent layer through the calls it already makes.
    "agents",
)
WRITABLE_CONFIG_KEYS = frozenset(EXPOSED_CONFIG_KEYS)
PATH_CONFIG_KEYS = frozenset({"workspace", "state_dir", "skills_dir", "agents_dir"})

# Which AGENTD_* env var (if any) OVERRIDES each exposed knob at boot. config.get reports the ones
# currently set so the UI can mark that field read-only ("pinned by <VAR> in .env") — otherwise a
# save silently reverts on restart. MUST mirror the `if os.environ.get("AGENTD_…")` handlers in
# config.py (only keys config.py actually consumes belong here — e.g. `model`/tool models are
# CONFIG-ONLY, so AGENTD_MODEL is dead and deliberately absent). Display-only: an omission just
# means a field isn't flagged, never wrong data.
EXPOSED_KEY_ENV = {
    "agent_name": "AGENTD_AGENT_NAME",
    "reasoning_effort": "AGENTD_REASONING",
    "max_turns": "AGENTD_MAX_TURNS",
    "model_fallbacks": "AGENTD_MODEL_FALLBACKS",
    "llm_idle_timeout_seconds": "AGENTD_LLM_IDLE_TIMEOUT",
    "llm_request_timeout_seconds": "AGENTD_LLM_REQUEST_TIMEOUT",
    "host": "AGENTD_HOST",
    "port": "AGENTD_PORT",
    "workspace": "AGENTD_WORKSPACE",
    "state_dir": "AGENTD_STATE_DIR",
    "tool_timeout_default": "AGENTD_TOOL_TIMEOUT",
    "tool_retries_default": "AGENTD_TOOL_RETRIES",
    "tools_enabled": "AGENTD_TOOLS_ENABLED",
    "tools_disabled": "AGENTD_TOOLS_DISABLED",
    "verify_tool": "AGENTD_VERIFY_TOOL",
    "completeness_check": "AGENTD_COMPLETENESS_CHECK",
    "computer_enabled": "AGENTD_COMPUTER_ENABLED",
    "execution_contract": "AGENTD_EXECUTION_CONTRACT",
    "subagents_enabled": "AGENTD_SUBAGENTS",
    "subagent_max_depth": "AGENTD_SUBAGENT_MAX_DEPTH",
    "diagnostics_upload": "AGENTD_DIAGNOSTICS_UPLOAD",
    "memory_enabled": "AGENTD_MEMORY",
    "memory_auto_recall": "AGENTD_MEMORY_AUTO_RECALL",
    "memory_auto_recall_limit": "AGENTD_MEMORY_AUTO_RECALL_LIMIT",
    "skill_workshop": "AGENTD_SKILL_WORKSHOP",
    "mcp_workshop": "AGENTD_MCP_WORKSHOP",
    "agent_messaging_enabled": "AGENTD_AGENT_MESSAGING",
    "autonomy_enabled": "AGENTD_AUTONOMY",
    "heartbeat_default_interval": "AGENTD_HEARTBEAT_INTERVAL",
    "heartbeat_active_hours": "AGENTD_HEARTBEAT_HOURS",
    "notify_enabled": "AGENTD_NOTIFY",
    "safe_to_send_check": "AGENTD_SAFE_TO_SEND",
    "workspace_index_enabled": "AGENTD_WORKSPACE_INDEX",
    "resource_manager_enabled": "AGENTD_RESOURCES",
    "resource_vision_enabled": "AGENTD_RESOURCE_VISION",
    "resource_summarize_enabled": "AGENTD_RESOURCE_SUMMARIZE",
    "scratch_ttl_hours": "AGENTD_SCRATCH_TTL_HOURS",
    "context_max_messages": "AGENTD_CONTEXT_MAX",
    "event_log_enabled": "AGENTD_EVENT_LOG",
    "parallel_search_enabled": "AGENTD_PARALLEL_SEARCH",
    "google_account": "AGENTD_GOOGLE_ACCOUNT",
    "public_url": "AGENTD_PUBLIC_URL",
    "webhook_host": "AGENTD_WEBHOOK_HOST",
    "webhook_port": "AGENTD_WEBHOOK_PORT",
    "skills_relevance_enabled": "AGENTD_SKILLS_RELEVANCE_ENABLED",
    "app_hosts": "AGENTD_APP_HOSTS",
}

# Curated model options offered as a dropdown in the settings UI (display name -> litellm id),
# so a user picks a model by name instead of typing an id. The config's own `model_catalog`
# extends this, and whatever models are actually in use are always merged in (below), so a
# custom/uncommon model never disappears from the picker.
DEFAULT_MODEL_CATALOG = (
    {"value": "gemini/gemini-pro-latest", "label": "Gemini Pro (latest)"},
    {"value": "gemini/gemini-flash-latest", "label": "Gemini Flash (latest)"},
    {"value": "gemini/gemini-flash-lite-latest", "label": "Gemini Flash Lite (latest)"},
    {"value": "gemini/gemini-3.6-flash", "label": "Gemini 3.6 Flash"},
    {"value": "gemini/gemini-3.5-flash", "label": "Gemini 3.5 Flash"},
    {"value": "gemini/gemini-3.5-flash-lite", "label": "Gemini 3.5 Flash Lite"},
    {"value": "gemini/gemini-3.1-flash-lite", "label": "Gemini 3.1 Flash Lite"},
    {"value": "gemini/gemini-3-pro-preview", "label": "Gemini 3 Pro"},
    {"value": "gemini/gemini-3-flash-preview", "label": "Gemini 3 Flash"},
    {"value": "gemini/gemini-2.5-flash-lite", "label": "Gemini 2.5 Flash Lite"},
    {"value": "gemini/gemini-2.0-flash-lite", "label": "Gemini 2.0 Flash Lite"},
    {"value": "gemini/gemini-3.1-pro-preview", "label": "Gemini 3.1 Pro"},
    {"value": "gemini/gemini-2.5-pro", "label": "Gemini 2.5 Pro"},
    {"value": "gemini/gemini-2.5-flash", "label": "Gemini 2.5 Flash"},
    {"value": "gemini/gemini-2.0-flash", "label": "Gemini 2.0 Flash"},
    {"value": "anthropic/claude-opus-4-8", "label": "Claude Opus 4.8"},
    {"value": "anthropic/claude-sonnet-5", "label": "Claude Sonnet 5"},
    {"value": "anthropic/claude-haiku-4-5", "label": "Claude Haiku 4.5"},
    {"value": "anthropic/claude-3-5-sonnet-latest", "label": "Claude 3.5 Sonnet"},
    {"value": "openai/gpt-5.6-sol", "label": "GPT-5.6 Sol"},
    {"value": "openai/gpt-5.6-luna", "label": "GPT-5.6 Luna"},
    {"value": "openai/gpt-5.6-terra", "label": "GPT-5.6 Terra"},
    {"value": "openai/gpt-5.5-pro", "label": "GPT-5.5 Pro"},
    {"value": "openai/gpt-5.5", "label": "GPT-5.5"},
    {"value": "openai/gpt-5.4-pro", "label": "GPT-5.4 Pro"},
    {"value": "openai/gpt-5.4", "label": "GPT-5.4"},
    {"value": "openai/gpt-5.4-mini", "label": "GPT-5.4 mini"},
    {"value": "openai/gpt-5.4-nano", "label": "GPT-5.4 nano"},
    {"value": "openai/gpt-5.2-pro", "label": "GPT-5.2 Pro"},
    {"value": "openai/gpt-5.2", "label": "GPT-5.2"},
    {"value": "openai/gpt-5.1", "label": "GPT-5.1"},
    {"value": "openai/gpt-5-pro", "label": "GPT-5 Pro"},
    {"value": "openai/gpt-5", "label": "GPT-5"},
    {"value": "openai/gpt-5-mini", "label": "GPT-5 mini"},
    {"value": "openai/gpt-4.1", "label": "GPT-4.1"},
    {"value": "openai/gpt-4o", "label": "GPT-4o"},
    {"value": "openai/o3", "label": "o3"},
    {"value": "deepseek/deepseek-chat", "label": "DeepSeek V3"},
    {"value": "deepseek/deepseek-reasoner", "label": "DeepSeek R1"},
    {"value": "xai/grok-4", "label": "Grok 4"},
    {"value": "xai/grok-3", "label": "Grok 3"},
    {"value": "groq/llama-3.3-70b-versatile", "label": "Llama 3.3 70B · Groq"},
    {"value": "mistral/mistral-large-latest", "label": "Mistral Large"},
)
# IMAGE-GENERATION models (for tools whose model_kind is "image-gen", e.g. generate_artwork). These
# OUTPUT pixels — a text/vision model here fails. Kept a separate list so an image tool's dropdown
# never offers a text model (and vice-versa). `provider` is the backend SDK the tool should use.
IMAGE_MODEL_CATALOG = (
    {
        "value": "gemini/gemini-3-pro-image",
        "label": "Nano Banana Pro (Gemini 3 Pro Image)",
        "group": "Google",
        "provider": "gemini",
    },
    {
        "value": "gemini/gemini-2.5-flash-image",
        "label": "Nano Banana (Gemini 2.5 Flash Image)",
        "group": "Google",
        "provider": "gemini",
    },
    {
        "value": "black-forest-labs/flux-1.1-pro",
        "label": "FLUX 1.1 Pro",
        "group": "FLUX",
        "provider": "replicate",
    },
    {
        "value": "black-forest-labs/flux-schnell",
        "label": "FLUX schnell (fast)",
        "group": "FLUX",
        "provider": "replicate",
    },
)
# EMBEDDING models (for tools whose model_kind is "embedding", e.g. memory search).
EMBEDDING_MODEL_CATALOG = (
    {"value": "gemini/text-embedding-004", "label": "Gemini text-embedding-004", "group": "Google"},
    {"value": "openai/text-embedding-3-small", "label": "OpenAI 3-small", "group": "OpenAI"},
    {"value": "openai/text-embedding-3-large", "label": "OpenAI 3-large", "group": "OpenAI"},
)
_PROVIDER_LABEL = {
    "gemini": "Google",
    "google": "Google",
    "vertex_ai": "Google",
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "azure": "OpenAI",
    "deepseek": "DeepSeek",
    "xai": "xAI",
    "groq": "Groq",
    "mistral": "Mistral",
    "openrouter": "OpenRouter",
    "together": "Together",
    "fireworks": "Fireworks",
    "ollama": "Ollama",
}
# Which env key(s) a provider needs before its models can actually run. () => local/no key
# (always available); a prefix absent here is treated as "unknown provider" => not filtered
# out (could be a custom endpoint). Used to hide models a user has no key for.
_PROVIDER_KEY_ENV = {
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "google": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "azure": ("AZURE_API_KEY", "OPENAI_API_KEY"),
    "deepseek": ("DEEPSEEK_API_KEY",),
    "xai": ("XAI_API_KEY",),
    "groq": ("GROQ_API_KEY",),
    "mistral": ("MISTRAL_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",),
    "together": ("TOGETHER_API_KEY",),
    "fireworks": ("FIREWORKS_API_KEY",),
    "ollama": (),
}


def _provider_prefix(model_id: str) -> str:
    return model_id.split("/", 1)[0].lower() if "/" in model_id else ""


def _provider_group(model_id: str) -> str:
    return _PROVIDER_LABEL.get(_provider_prefix(model_id), "Other")


def _provider_has_key(model_id: str) -> bool:
    """True if the provider behind this model is usable right now (a required key is present,
    or it needs none / is an unknown custom provider we won't second-guess).

    Platform-keys mode: when a model proxy is configured, the provider KEYS live on the proxy
    (this daemon holds only the proxy's master key, never GEMINI_API_KEY/DEEPSEEK_API_KEY/…). The
    proxy is the authority on what's runnable, so nothing should be hidden for lack of a LOCAL key
    — otherwise a hosted daemon filters out its entire catalog and the picker comes up empty."""
    try:
        from agent_runtime.infrastructure.llm import model_proxy

        if model_proxy.enabled():
            return True
    except Exception:
        pass
    envs = _PROVIDER_KEY_ENV.get(_provider_prefix(model_id))
    if not envs:  # unknown provider or keyless (ollama) => don't hide
        return True
    return any(os.environ.get(e) for e in envs)


# Built-in per-KIND option SEEDS. These are ONLY a fresh-install fallback — `config.model_catalog`
# is the source of truth. To add/remove a model in ANY picker, edit config (tag an entry with `kind`),
# NEVER this code. Kinds: text | vision | image | embedding.
_SEED_CATALOGS = {
    "text": DEFAULT_MODEL_CATALOG,
    "vision": DEFAULT_MODEL_CATALOG,  # multimodal text models double as vision
    "image-gen": IMAGE_MODEL_CATALOG,
    "embedding": EMBEDDING_MODEL_CATALOG,
}


def _has_kind(cfg, kind: str) -> bool:
    return any(
        isinstance(e, dict) and str(e.get("kind") or "text").lower() == kind
        for e in (getattr(cfg, "model_catalog", None) or [])
    )


def _catalog_for(cfg, kind: str, forced=()) -> list:
    """The dropdown options for one model KIND — CONFIG-FIRST. The menu is ``config.model_catalog``
    (the single source of truth): entries whose ``kind`` matches (a bare string, or a dict with no
    ``kind``, counts as ``text``). If the config declares none for this kind, the built-in seed is
    used (fresh-install fallback only). Menu models are filtered to providers whose key is present;
    ``forced`` values (models actually in use) are always kept; result is deduped + provider-grouped.
    Adding a model to any picker is therefore a CONFIG edit, never a code edit."""
    seen: dict = {}

    def add(value, label=None, group=None, provider=None, force=False):
        v = (str(value) if value else "").strip()
        if not v or v in seen:
            return
        if not force and not _provider_has_key(v):  # hide a menu model with no key
            return
        seen[v] = {
            "value": v,
            "label": (label or v),
            "group": (group or _provider_group(v)),
            **({"provider": provider} if provider else {}),
        }

    menu = getattr(cfg, "model_catalog", None) or []
    tagged = []
    for e in menu:
        if isinstance(e, dict) and str(e.get("kind") or "text").lower() == kind:
            tagged.append(e)
        elif isinstance(e, str) and kind == "text":
            tagged.append({"value": e})
    for e in tagged or _SEED_CATALOGS.get(kind, ()):
        add(e.get("value") or e.get("id"), e.get("label"), e.get("group"), e.get("provider"))
    for v in forced:
        add(v, force=True)
    return list(seen.values())


def _build_model_catalog(cfg) -> list:
    """The TEXT model picker (back-compat name): the text-kind catalog with the brain, failover, and
    cost-efficiency models force-included so a configured model never vanishes from the picker."""
    ce = getattr(cfg, "cost_efficiency", None) or {}
    forced = [getattr(cfg, "model", None), *(getattr(cfg, "model_fallbacks", None) or [])]
    if isinstance(ce, dict):
        forced += [ce.get("text_model"), ce.get("vision_model")]
    return _catalog_for(cfg, "text", forced)


def _kind_catalogs(cfg) -> dict:
    """Per-KIND option lists (all config-first) so each tool's dropdown offers only the right kind of
    model. In-use tool models are forced in so nothing a tool actually uses disappears; vision reuses
    the text picker unless the config explicitly declares vision-kind models."""
    plugins = getattr(cfg, "plugins", None) or {}

    def in_use(pid, tool):
        t = ((plugins.get(pid) or {}).get("tools") or {}).get(tool) or {}
        return [t["model"]] if t.get("model") else []

    text = _build_model_catalog(cfg)
    vision_forced = in_use("vision", "read_labels_from_image") + in_use("vision", "verify_figure")
    return {
        "models": text,  # back-compat key (text)
        "text": text,
        "vision": _catalog_for(cfg, "vision", vision_forced) if _has_kind(cfg, "vision") else text,
        "image-gen": _catalog_for(cfg, "image-gen", in_use("figure-art", "generate_artwork")),
        "embedding": _catalog_for(cfg, "embedding"),
    }


def _config_file_path():
    """The agentd.config.json this daemon reads (first existing candidate), or the
    default write location when none exists yet. Same resolver load_config uses."""
    from agent_runtime import runtime_paths

    return runtime_paths.active_config_file()


def _json_safe(value):
    """Coerce a live Config value to something JSON-serializable (Path -> str, recurse)."""
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _persist_config_patch(patch: dict) -> tuple[bool, str]:
    """Merge ``patch`` into agentd.config.json, PRESERVING every other key. Best-effort."""
    import json

    path = _config_file_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        data = {}
    data.update(patch)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return True, str(path)
    except OSError as e:  # noqa: BLE001 — persistence is best-effort
        log.warning("could not persist config to %s: %s", path, e)
        return False, str(path)


def _user_env_file() -> EnvFile:
    """The ``.env`` beside this daemon's config — the one secret channel.

    The rewriting logic used to live here as a module function. It is infrastructure (it writes a
    file), the gateway had four call sites reaching through it, and the sign-in store needs the
    same behaviour, so it moved to ``infrastructure/env_file.py``. This is just where the path
    comes from."""
    from agent_runtime import runtime_paths

    return EnvFile(runtime_paths.user_env_file())


@dataclass
class Gateway:
    """Transport only: accepts WebSocket frames and delegates work to the injected
    ``service`` (the AgentService use-case). It is built by main/container.py — it no
    longer composes anything itself."""

    config: Config
    service: AgentService  # injected use-case (does the work)
    browser_manager: object | None = None  # injected; closed on shutdown
    mcp_provider: object | None = None  # injected; discovered at startup, closed on shutdown
    # Per-agent DECLARED servers (agent.toml [[mcp]]) — a different thing from mcp_provider, which
    # serves the machine-wide config.mcp_servers list. Held here so a settings write can make the
    # affected agents re-dial, and so a page can see why a server is not up.
    mcp_connector: object | None = None
    # OAuthService — third-party sign-ins an agent declares ([[oauth]]). None on a build without
    # it; every handler then says so rather than pretending a connection is merely absent.
    oauth_service: object | None = None
    registry: object | None = None  # injected; the agent registry (for the scheduler)
    task_store: object | None = None  # injected; durable cron ledger (Phase 2b), or None
    memory_bank: object | None = None  # injected; long-term memory store (S4), or None
    event_log: object | None = None  # injected; durable per-run event stream, or None
    credential_store: object | None = None  # injected; login vault (/connect form writes here)
    connect_tokens: object | None = None  # injected; one-time /connect-link tokens
    safe_to_send_gate: object | None = None  # injected; out-of-band privacy gate on channel replies
    notifier: object | None = None  # built in serve(); outbound user notifications (5a)
    channels: list = field(default_factory=list)  # active messaging channels (5b), built in serve()
    channel_notifiers: list = field(
        default_factory=list
    )  # ChannelNotifier per notify-capable channel
    subagent_active: int = 0  # in-flight sub-agent runs (runaway guard, S8)
    webhook_server: object | None = None  # the WebhookServer (set in serve); hosts task hooks
    # M2 auth: the bearer token clients must present ("" => open, the test/dev default).
    # Set by serve() from config (gateway_auth/gateway_token) — never at construction, so
    # unit tests that drive _handle_conn directly are unaffected.
    auth_token: str = ""
    # M4: the marketplace service — built lazily on the first marketplace.* call
    # (mirrors _ensure_mcp_provider), wired to broadcast progress + hot-reload.
    # This one is the NO-ACCOUNT service (desktop/local, and anything a test injects).
    marketplace: object | None = None
    # account id -> that account's marketplace service. Hosted only: an install writes files, so
    # each account composes the service against its OWN agents/plugins/state dirs. Empty on
    # desktop, where account_id() is never set. See _marketplace().
    account_marketplaces: dict = field(default_factory=dict)
    # agent id -> {"task", "error", "at"} for visitor-triggered web-app installs/updates
    # (hosted only; see _serve_app / _web_app_bootstrap). Installs land in the SHARED catalogue:
    # /apps static serving is unauthenticated, so one copy serves every visitor.
    web_app_syncs: dict = field(default_factory=dict)
    clients: set[ServerConnection] = field(default_factory=set)
    # agent-scoped app connections: ws -> the ONE agent id the connection is limited to
    # (see APP_SCOPED_METHODS + _scoped_event_allowed). Absent = a full host connection.
    client_scopes: dict = field(default_factory=dict)
    # PUBLIC connections (hosted): unauthenticated, admitted only because their scope's
    # [app] declares public = true. Always ALSO in client_scopes; further limited to
    # PUBLIC_APP_METHODS + the agent's [app] public_tools.
    client_public: set = field(default_factory=set)
    # TENANT ISOLATION, receive side: ws -> the identity set this connection authenticated to
    # (ownership.callers — an account, plus "local" on a desktop; frozenset() for anonymous
    # public sockets). _send_all delivers a frame only where ownership.may_observe says the
    # frame's acting owner is among these. Populated for EVERY connection at _handle_conn.
    client_identities: dict = field(default_factory=dict)
    # global in-flight cap for public tools.invoke (created lazily on the running loop)
    _public_invoke_sem: object | None = None
    runs: dict[str, RunHandle] = field(default_factory=dict)  # session_key -> handle
    idempotency: dict[str, str] = field(default_factory=dict)  # key -> run_id

    # ------------------------------------------------------------------ serve

    async def serve(self) -> None:
        # M2: ONE daemon per user — a live rendezvous file means another gateway owns
        # this machine's agentd; refuse loudly instead of fighting over ports/state.
        existing = lifecycle.find_running()
        if existing is not None and existing.pid != os.getpid():
            raise SystemExit(
                f"agentd is already running (pid {existing.pid}, {existing.ws_url}) — "
                f"attach with `agentd chat` or stop it with `agentd stop`."
            )
        # THE FILE CAN LIE BY BEING ABSENT. find_running() reads the rendezvous file, so a daemon
        # whose file was removed while it still holds the port is invisible to the guard above —
        # and we then bind, fail, and print a 30-line traceback whose real cause (errno 10048) is
        # the last line. Ask the PORT as well, and say the one useful thing instead.
        if existing is None and lifecycle.port_open(self.config.host, self.config.port):
            from agent_runtime import runtime_paths

            raise SystemExit(
                f"port {self.config.port} on {self.config.host} is already in use, but "
                f"{runtime_paths.gateway_file()} does not describe a running agentd. Almost "
                f"always another agentd whose rendezvous file was deleted: stop it (Task Manager: "
                f"python.exe / agentd) and start again, or set AGENTD_PORT to a free port."
            )
        # M2 auth: mint (or adopt) the bearer token clients must present. The token
        # travels ONLY via the 0600 rendezvous file — never argv, never logs.
        if getattr(self.config, "gateway_auth", False):
            self.auth_token = getattr(self.config, "gateway_token", "") or lifecycle.mint_token()
        # LAYOUT FIRST, before anything reads a path. Folds any account still on the split
        # `installed/agents` + `agents` trees into the single `agents/<id>/` one (definition +
        # workspace + sessions together). Idempotent and silent once done; done here because a
        # daemon start is the only moment no connection is mid-read.
        user_state.migrate_legacy_account_layout(self.config.state_dir)
        # Fast, in-process registrations happen BEFORE bind (cheap, chat depends on them)…
        self._build_subagents()  # the spawn_subagent tool (S8), if enabled
        self._build_agent_messaging()  # message_agent: talk to OTHER persistent agents (A5)
        self._build_add_mcp()  # add_mcp: connect an MCP server by chatting (B2)
        # …but everything SLOW or external is deferred until AFTER the port is open (see
        # _deferred_startup): a cold external MCP server (uvx download, OAuth dance) used to
        # hold the bind for minutes, which stalls every client and the desktop supervisor.
        # Clients can chat with native tools immediately; MCP tools join the catalog live.
        scheduler_task = poller_task = webhook_task = None
        startup_task: asyncio.Task | None = None

        def _adopt_background(tasks: tuple) -> None:
            nonlocal scheduler_task, poller_task, webhook_task
            scheduler_task, poller_task, webhook_task = tasks

        async with serve(
            self._handle_conn,
            self.config.host,
            self.config.port,
            process_request=self._http_request,
            max_size=MAX_WS_FRAME,
        ):
            lifecycle.write_gateway_file(
                lifecycle.GatewayInfo(
                    host=self.config.host,
                    port=self.config.port,
                    pid=os.getpid(),
                    token=self.auth_token,
                    version=__version__,
                    started_at=datetime.now().isoformat(timespec="seconds"),
                )
            )
            log.info(
                "listening on ws://%s:%s (auth %s)",
                self.config.host,
                self.config.port,
                "on" if self.auth_token else "off",
            )
            print(f"agentd listening on ws://{self.config.host}:{self.config.port}")
            print(f"model: {_effective_model(self.config)} | workspace: {self.config.workspace}")
            startup_task = asyncio.create_task(
                self._deferred_startup(_adopt_background), name="deferred-startup"
            )
            try:
                await asyncio.Future()  # run forever
            finally:
                lifecycle.clear_gateway_file(only_pid=os.getpid())
                if startup_task is not None:
                    startup_task.cancel()
                if scheduler_task is not None:
                    scheduler_task.cancel()
                if poller_task is not None:
                    poller_task.cancel()
                if webhook_task is not None:
                    webhook_task.cancel()
                if self.task_store is not None:
                    self.task_store.close()
                if self.event_log is not None:
                    self.event_log.close()
                if self.browser_manager is not None:
                    await self.browser_manager.close()
                if self.mcp_provider is not None:
                    await self.mcp_provider.aclose()

    async def _deferred_startup(self, adopt_background) -> None:
        """Everything that used to run before bind but doesn't have to: connect external
        MCP servers (slow, cold-start-prone), then the pieces that depend on their tools
        (channels, notifier), then the background services. Order preserved exactly;
        only the bind moved earlier. ``adopt_background`` hands the started tasks back
        to serve() so shutdown still cancels them."""
        try:
            await self._discover_mcp_tools()  # connect external MCP servers, add their tools
            self._build_channels()  # messaging channels (5b) — email needs MCP tools first
            self._build_notifier()  # outbound notifications (client-push + durable + channels)
            adopt_background(
                (
                    self._start_scheduler(),  # autonomy (heartbeat); None if disabled
                    self._start_channel_poller(),  # inbound poll channels; None if none
                    self._start_webhook_server(),  # push channels (LINE) + task hooks (/hook/<id>)
                )
            )
            self._build_create_webhook()  # create_webhook: mint task triggers by chatting (D)
            # fill in any missing agent taglines/suggestions (one-time, per agent)
            asyncio.create_task(self._maybe_generate_presentations(), name="agent-presentation")
            log.info("deferred startup complete (MCP + channels + background services)")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — startup extras must never kill the gateway
            log.exception("deferred startup failed — core chat keeps serving")

    def _start_scheduler(self):
        """Start the shared heartbeat scheduler — only when autonomy is enabled and a
        registry is available. Returns the task (cancelled on shutdown), or None."""
        if not getattr(self.config, "autonomy_enabled", False) or self.registry is None:
            return None
        from agent_runtime.infrastructure.autonomy import HeartbeatScheduler

        scheduler = HeartbeatScheduler(
            self.registry,
            self._post_heartbeat,
            enabled=True,
            default_interval=self.config.heartbeat_default_interval,
            active_hours=self.config.heartbeat_active_hours,
            task_store=self.task_store,
            fire_task=self._post_cron,  # cron (2b)
        )
        return asyncio.create_task(scheduler.run(), name="autonomy-scheduler")

    # ------------------------------------------------------------- channels (5b)

    def _build_channels(self) -> None:
        """Build messaging channels from config (default none). Email channels invoke
        the Gmail MCP via _invoke_tool. A channel with `notify_to` also becomes a
        ChannelNotifier so notifications reach you on it (reuses the one transport)."""
        cfgs = getattr(self.config, "channels", None) or []
        if not cfgs:
            return
        from agent_runtime.infrastructure.channels import build_channel
        from agent_runtime.infrastructure.notify import ChannelNotifier

        for c in cfgs:
            try:
                ch = build_channel(c, self._invoke_tool)
            except Exception:  # noqa: BLE001 — a bad channel never blocks serving
                log.warning("failed to build channel %s", c, exc_info=True)
                continue
            if ch is None:
                continue
            self.channels.append(ch)
            notify_to = (c.get("notify_to") or "").strip()
            if notify_to:
                self.channel_notifiers.append(ChannelNotifier(ch, notify_to))
            log.info("channel ready: %s -> agent %s", ch.name, getattr(ch, "agent_id", "?"))

    def _build_subagents(self) -> None:
        """Register the spawn_subagent tool (S8) when enabled — the agent can delegate a
        subtask to a fresh child run and get its result back."""
        if not getattr(self.config, "subagents_enabled", False):
            return
        from agent_runtime.infrastructure.tools.guard import GuardedTool, resolve_policy
        from agent_runtime.infrastructure.tools.subagent_tool import SpawnSubagentTool

        tool = SpawnSubagentTool(self._spawn_subagent)
        self.service.add_tools([GuardedTool(tool, resolve_policy(self.config, tool))])
        log.info(
            "sub-agents enabled (max %d concurrent, max depth %d)",
            getattr(self.config, "subagent_max", 4),
            min(5, max(1, int(getattr(self.config, "subagent_max_depth", 1) or 1))),
        )

    def _build_agent_messaging(self) -> None:
        """Register message_agent (A5) when enabled — call ANOTHER persistent agent and get its
        reply (its own ongoing session, so it remembers). Gated by agent_messaging_enabled."""
        if not getattr(self.config, "agent_messaging_enabled", False):
            return
        from agent_runtime.infrastructure.tools.guard import GuardedTool, resolve_policy
        from agent_runtime.infrastructure.tools.message_agent_tool import MessageAgentTool

        tool = MessageAgentTool(self._message_agent)
        self.service.add_tools([GuardedTool(tool, resolve_policy(self.config, tool))])
        log.info("agent-to-agent messaging enabled (message_agent)")

    def _build_add_mcp(self) -> None:
        """Register add_mcp (B2) when enabled — the agent connects an MCP server by chatting
        (wraps the same _mcp_add machinery the mcp.add RPC uses). Gated by mcp_workshop."""
        if not getattr(self.config, "mcp_workshop", False):
            return
        from agent_runtime.infrastructure.tools.add_mcp_tool import AddMcpTool
        from agent_runtime.infrastructure.tools.guard import GuardedTool, resolve_policy

        tool = AddMcpTool(self._mcp_add)
        self.service.add_tools([GuardedTool(tool, resolve_policy(self.config, tool))])
        log.info("add_mcp enabled (agent can connect MCP servers)")

    async def _message_agent(self, target_id: str, message: str) -> str:
        """Run a turn on ANOTHER agent's PERSISTENT peer session and return its reply (A5).

        Distinct from _spawn_subagent: the target runs on a durable ``agent:<target>:peer:<caller>``
        session (so it accumulates state with this caller), as ITS own agent (identity/workspace/
        skills). Honors the caller's ``[subagents] allow`` scope; one-level only (loop guard)."""
        ctx = current_run_context()
        caller = (ctx.agent_id if ctx else None) or "main"
        parent_key = ctx.session_key if ctx else ""
        if ":peer:" in parent_key:  # a messaged agent can't chain further
            return "agent-to-agent messaging cannot chain further (loop guard)."
        if not target_id:
            return "message_agent needs a target agent id."
        if target_id == caller:
            return "cannot message yourself — just do the work, or use spawn_subagent."
        if self.registry is not None and target_id not in self.registry.list_ids():
            return f"unknown agent: {target_id}"
        if self.registry is not None:  # honor the caller's delegation allowlist
            from agent_runtime.domain.agent import _matches

            try:
                spec = self.registry.get(caller)
            except KeyError:
                spec = None
            allow = getattr(spec, "subagents_allow", None)
            if allow is not None and not any(_matches(target_id, p) for p in allow):
                return (
                    f"'{caller}' may not message '{target_id}' "
                    f"(allowed: {', '.join(allow) or 'none'})."
                )
        session_key = f"agent:{target_id}:peer:{caller}"
        # Layer B: a delegation FROM a project chat stays in the project (child meta inherits
        # projectId), so the target agent works in the project's shared workspace.
        self._inherit_project(caller, parent_key, target_id, session_key)
        handle = RunHandle(
            run_id=uuid.uuid4().hex[:12],
            session_key=session_key,
            abort=asyncio.Event(),
            client_id=None,
            parent_session_key=parent_key or None,
        )  # relay progress to the caller
        await asyncio.create_task(
            self._run(handle, message, mode=RunMode.INTERACTIVE, agent_id=target_id)
        )
        return self._last_answer(target_id, session_key) or "(the agent produced no reply)"

    async def _spawn_subagent(self, agent_id: str | None, task: str) -> str:
        """Run a self-contained CHILD agent turn and return its final answer. Called from
        within the parent's tool execution; the child runs as its own asyncio.Task so its
        run-context (contextvar) never clobbers the parent's. Capped + depth-limited."""
        ctx = current_run_context()
        parent_agent = (ctx.agent_id if ctx else None) or "main"
        parent_key = ctx.session_key if ctx else ""
        # Depth limit (A3): configurable nesting (1 = no nesting), hard ceiling 5. A run already
        # at max depth cannot spawn further — mirrors OpenClaw's maxSpawnDepth.
        max_depth = min(5, max(1, int(getattr(self.config, "subagent_max_depth", 1) or 1)))
        depth = _subagent_depth(parent_key)
        if depth >= max_depth:
            return (
                f"sub-agents cannot spawn deeper here (already at depth {depth}, max {max_depth})."
            )
        cap = int(getattr(self.config, "subagent_max", 4))
        if self.subagent_active >= cap:
            return f"sub-agent limit reached ({cap} concurrent); try again when some finish."

        child_agent = agent_id or parent_agent
        if self.registry is not None and child_agent not in self.registry.list_ids():
            return f"unknown agent: {child_agent}"
        # Allowlist (A4): when delegating to a NAMED other agent, honor the caller's [subagents]
        # allow scope (ids/globs). None => unrestricted; spawning oneself is always allowed.
        if agent_id and child_agent != parent_agent and self.registry is not None:
            from agent_runtime.domain.agent import _matches

            try:
                spec = self.registry.get(parent_agent)
            except KeyError:
                spec = None
            allow = getattr(spec, "subagents_allow", None)
            if allow is not None and not any(_matches(child_agent, p) for p in allow):
                return (
                    f"'{parent_agent}' may not delegate to '{child_agent}' "
                    f"(allowed: {', '.join(allow) or 'none'})."
                )
        session_key = f"agent:{child_agent}:sub:{depth + 1}:{uuid.uuid4().hex[:8]}"
        # Layer B: sub-work spawned from a project chat inherits the project (shared workspace).
        self._inherit_project(parent_agent, parent_key, child_agent, session_key)
        handle = RunHandle(
            run_id=uuid.uuid4().hex[:12],
            session_key=session_key,
            abort=asyncio.Event(),
            client_id=None,
            parent_session_key=parent_key or None,
        )  # relay progress to parent
        self.subagent_active += 1
        try:
            # own Task => its own copied context => child set_run_context can't leak to parent
            await asyncio.create_task(
                self._run(handle, task, mode=RunMode.INTERACTIVE, agent_id=child_agent)
            )
        finally:
            self.subagent_active -= 1
        return self._last_answer(child_agent, session_key) or "(sub-agent produced no answer)"

    def _start_channel_poller(self):
        """One shared loop polling every channel for inbound messages. None if no channels."""
        if not self.channels:
            return None
        from agent_runtime.infrastructure.channels import ChannelPoller

        poller = ChannelPoller(
            self.channels,
            self._fire_channel,
            interval=float(getattr(self.config, "channel_poll_seconds", 15.0)),
        )
        return asyncio.create_task(poller.run(), name="channel-poller")

    def _start_webhook_server(self):
        """One HTTP server hosting: PUSH channels (LINE etc.) on their own paths, the generic
        TASK-trigger route ``/hook/<id>`` (D), and the /connect form. Channel events fire through
        ``_fire_channel`` (conversational); task hooks run an agent via ``_run_task`` (no reply).
        None if nothing needs the server."""
        push = [c for c in self.channels if getattr(c, "webhook_path", None)]
        connect_on = self.credential_store is not None and self.connect_tokens is not None
        task_hooks_on = bool(getattr(self.config, "webhooks", None)) or bool(
            getattr(self.config, "webhook_workshop", False)
        )
        if not push and not connect_on and not task_hooks_on:  # nothing needs the HTTP server
            return None
        from agent_runtime.infrastructure.channels.webhook import WebhookServer

        server = WebhookServer(
            push,
            self._fire_channel,
            host=getattr(self.config, "webhook_host", "0.0.0.0"),
            port=int(getattr(self.config, "webhook_port", 8788)),
            credential_store=self.credential_store if connect_on else None,
            connect_tokens=self.connect_tokens if connect_on else None,
            run_task=self._run_task if task_hooks_on else None,
            hooks=list(getattr(self.config, "webhooks", None) or []),
        )
        self.webhook_server = server  # so create_webhook can add hooks live
        return asyncio.create_task(server.run(), name="webhook-server")

    async def _run_task(self, agent_id: str, task: str) -> None:
        """Run an agent with a one-off task from an external trigger (a webhook). Fire-and-forget
        on a dedicated ``agent:<id>:hook:<run>`` session — the agent acts (and can notify/cron if
        it needs to); no conversational reply is returned to the caller."""
        agent_id = (agent_id or "main").strip() or "main"
        if self.registry is not None and agent_id not in self.registry.list_ids():
            log.warning("webhook task: unknown agent '%s' — ignoring", agent_id)
            return
        session_key = f"agent:{agent_id}:hook:{uuid.uuid4().hex[:8]}"
        handle = RunHandle(
            run_id=uuid.uuid4().hex[:12],
            session_key=session_key,
            abort=asyncio.Event(),
            client_id=None,
        )
        await self._run(handle, task, mode=RunMode.INTERACTIVE, agent_id=agent_id)

    def _build_create_webhook(self) -> None:
        """Register create_webhook (D) when enabled — the agent mints a /hook/<id> URL by chatting.
        Gated by webhook_workshop. Needs the webhook server (started above) to add hooks live."""
        if not getattr(self.config, "webhook_workshop", False):
            return
        from agent_runtime.infrastructure.tools.create_webhook_tool import CreateWebhookTool
        from agent_runtime.infrastructure.tools.guard import GuardedTool, resolve_policy

        tool = CreateWebhookTool(self._create_webhook)
        self.service.add_tools([GuardedTool(tool, resolve_policy(self.config, tool))])
        log.info("create_webhook enabled (agent can mint webhook triggers)")

    async def _create_webhook(self, params: dict) -> dict:
        """Mint a task hook: a random id+secret bound to an agent, registered LIVE on the webhook
        server and persisted. Returns the URL + secret to paste into the external service."""
        import re
        import secrets

        if self.webhook_server is None:
            return {"created": False, "error": "webhook server not running"}
        agent = (params.get("agent") or "main").strip() or "main"
        if self.registry is not None and agent not in self.registry.list_ids():
            return {"created": False, "error": f"unknown agent: {agent}"}
        hid = (
            re.sub(r"[^a-z0-9-]+", "-", (params.get("id") or "").strip().lower()).strip("-")
            or f"hook-{secrets.token_hex(3)}"
        )
        if hid in {h.get("id") for h in (self.config.webhooks or [])}:
            return {"created": False, "error": f"a hook '{hid}' already exists"}
        hook = {"id": hid, "secret": secrets.token_urlsafe(24), "agent": agent}
        task = (params.get("task") or "").strip()
        if task:
            hook["task"] = task
        self.webhook_server.add_hook(hook)
        self.config.webhooks = list(self.config.webhooks or []) + [hook]
        persisted = _persist_webhooks(self.config)
        base = (
            getattr(self.config, "public_url", "")
            or f"http://{getattr(self.config, 'webhook_host', '0.0.0.0')}:"
            f"{getattr(self.config, 'webhook_port', 8788)}"
        ).rstrip("/")
        log.info("create_webhook '%s' -> agent '%s', persisted=%s", hid, agent, persisted)
        return {
            "created": True,
            "id": hid,
            "secret": hook["secret"],
            "url": f"{base}/hook/{hid}",
            "agent": agent,
            "persisted": persisted,
        }

    async def _invoke_tool(self, name: str, params: dict) -> str:
        """Invoke a registered (namespaced MCP) tool by name OUTSIDE the agent loop —
        lets a channel send/poll via an MCP (e.g. Gmail). Returns the tool's text."""
        tool = self.service.find_tool(name)
        if tool is None:
            raise RuntimeError(f"tool not available: {name}")
        result = await tool.execute(uuid.uuid4().hex[:8], params or {}, asyncio.Event())
        text = "".join(getattr(b, "text", "") for b in (result.content or []))
        if result.is_error:
            raise RuntimeError(text or f"{name} failed")
        return text

    async def _tools_invoke(
        self, params: dict, scope: str | None = None, public: bool = False
    ) -> dict:
        """Run ONE tool directly from a client (no agent/LLM turn) and return its text + rendered
        artifacts. Params: {name, params:{...}}. Three gates (docs/PROTOCOL.md §6):

        • HOST connections — only tools that self-declare `artifact_action` (canvas buttons),
          so a general client can't run arbitrary tools.
        • agent-SCOPED app connections — any tool the scoped AGENT itself is allowed
          (its tools.allow/deny), executed in that agent's context (workspace + per-agent
          model overrides). The app surface is exactly the agent's own capability surface.
        • PUBLIC connections — the author-declared `[app] public_tools` subset ONLY,
          checked ON TOP of the agent's own allow/deny (the gates stack, never replace).
        """
        from agent_runtime.infrastructure.files import resolve_artifacts
        from agent_runtime.infrastructure.plugins.catalog import _unwrap_tool

        name = (params.get("name") or "").strip()
        tool = self.service.find_tool(name, scope)  # scoped: the agent's OWN tools win
        if tool is None:
            raise RuntimeError(f"tool not available: {name}")
        run_ctx = None
        if scope:
            from agent_runtime.application.run_context import RunContext
            from agent_runtime.domain.agent import select_private_tools, select_tools

            try:
                spec = self.registry.get(scope) if self.registry is not None else None
            except KeyError:
                spec = None
            if spec is None:
                raise RuntimeError(f"unknown agent: {scope}")
            if public:
                allowed = ((getattr(spec, "app", None) or {}).get("public_tools")) or ()
                if name not in allowed:
                    raise RuntimeError(f"tool '{name}' is not publicly invokable")
            # an agent's own shipped tool is implicitly allowed (deny still wins); a shared
            # tool goes through the agent's normal allow/deny scope.
            own = getattr(_unwrap_tool(tool), "_agent_id", "") == scope
            permitted = (
                select_private_tools([tool], spec) if own else select_tools([tool], spec)
            )
            if not permitted:
                raise RuntimeError(f"tool '{name}' is not available to agent '{scope}'")
            # The invocation runs AS THE CALLER, not as the agent's folder: on a hosted daemon
            # the workspace is the caller's own per-agent one (the same resolution every run
            # uses), and the run carries the caller's tenant scope so a directly-invoked tool
            # is fenced exactly like a tool inside a turn. Desktop: acct is None, the scope is
            # empty ( = unrestricted) and the workspace is the agent's own — unchanged.
            acct = accounts.account_id() or ""
            workspace = (
                str(user_state.account_workspace(self.config.state_dir, acct, scope))
                if acct
                else str(getattr(spec, "workspace", "") or "")
            )
            read_roots, write_clamp = user_state.tenant_scope(
                self.config, acct, getattr(spec, "dir", None), workspace
            )
            run_ctx = RunContext(
                agent_id=scope,
                session_key=f"agent:{scope}:app",
                mode=RunMode.INTERACTIVE,
                workspace=workspace,
                plugins=getattr(spec, "plugins", None),
                read_roots=read_roots,
                write_clamp=write_clamp,
            )
        # find_tool returns the reliability WRAPPER (GuardedTool, real tool in `_inner`); the
        # self-declared `artifact_action` lives on the inner tool — unwrap to read it (same as the
        # catalog does), or the gate would reject every UI action. We still RUN the wrapper.
        elif not getattr(_unwrap_tool(tool), "artifact_action", None):
            raise RuntimeError(f"tool '{name}' is not invokable from the UI")

        async def _execute():
            return await tool.execute(
                uuid.uuid4().hex[:8], dict(params.get("params") or {}), asyncio.Event()
            )

        # public tier: one GLOBAL in-flight cap across all visitors (per-connection requests
        # are already serialized by _handle_conn's inline await) — a flood queues, not forks.
        if public:
            if self._public_invoke_sem is None:
                self._public_invoke_sem = asyncio.Semaphore(PUBLIC_INVOKE_CONCURRENCY)
            await self._public_invoke_sem.acquire()
        try:
            if run_ctx is not None:
                from agent_runtime.application.run_context import set_run_context

                # contextvars are task-local: run the tool in ITS OWN task so the scoped agent
                # context can never leak into this connection's later requests.
                async def _scoped():
                    set_run_context(run_ctx)
                    return await _execute()

                result = await asyncio.create_task(_scoped())
            else:
                result = await _execute()
        finally:
            if public and self._public_invoke_sem is not None:
                self._public_invoke_sem.release()
        text = "".join(getattr(b, "text", "") for b in (result.content or []))
        if result.is_error:
            raise RuntimeError(text or f"{name} failed")
        return {"text": text, "artifacts": resolve_artifacts(result.artifacts)}

    async def _fire_channel(self, channel, msg) -> None:
        """An inbound message arrived -> run the bound agent on a conversation-bound
        session and reply on the SAME channel. Busy-guarded per peer."""
        agent_id = getattr(channel, "agent_id", "main")
        session_key = f"agent:{agent_id}:{channel.name}:{msg.peer}"
        existing = self.runs.get(session_key)
        if existing is not None and existing.task is not None and not existing.task.done():
            return  # a run for this peer is already in flight; next poll picks it up
        handle = RunHandle(
            run_id=uuid.uuid4().hex[:12],
            session_key=session_key,
            abort=asyncio.Event(),
            client_id=None,
        )
        handle.task = asyncio.create_task(self._run_channel(handle, channel, msg, agent_id))
        self.runs[session_key] = handle
        log.info("channel %s: message from %s -> run %s", channel.name, msg.peer, handle.run_id)

    async def _run_channel(self, handle: RunHandle, channel, msg, agent_id: str) -> None:
        await self._run(handle, msg.text, mode=RunMode.CHANNEL, agent_id=agent_id)
        reply = self._last_answer(agent_id, handle.session_key)
        if reply:
            # EGRESS PRIVACY GATE: if this agent is tagged `audience = "external"` in its toml, an
            # independent judge verifies the reply is safe to send against the agent's OWN rules
            # BEFORE it leaves — blocked -> a safe replacement goes instead. Agents not tagged
            # external (and all interactive/websocket replies) are never gated.
            reply = await self._verify_safe_to_send(handle, agent_id, msg.text, reply)
            try:
                await channel.send(msg.peer, reply)
            except Exception:  # noqa: BLE001
                log.warning("channel reply send failed (%s)", channel.name, exc_info=True)

    async def _verify_safe_to_send(
        self, handle: RunHandle, agent_id: str, question: str, answer: str
    ) -> str:
        """Run the out-of-band safe-to-send gate on an outbound channel reply and return the
        text to actually send: the original answer if cleared, or a safe replacement if blocked.
        Applies ONLY to agents tagged `audience = "external"` in their toml; anything else
        (no gate built, agent unset / "internal" / other) passes through unchanged. Every
        decision is logged + recorded to the event log (audit: what was withheld and why)."""
        gate = self.safe_to_send_gate
        if gate is None:
            return answer
        spec = self._spec(agent_id)
        # Apply the gate ONLY to agents declared external-facing. Absent / "internal" / anything
        # else => not gated.
        if spec is None or spec.audience != "external":
            return answer
        from agent_runtime.application.interfaces.safe_to_send import SafeToSendContext

        verdict = await gate.check(
            SafeToSendContext(
                audience=spec.audience,
                policy=spec.instructions or "",
                conversation=self._recent_dialog(agent_id, handle.session_key),
                question=question,
                answer=answer,
            )
        )
        if verdict.safe:
            self._emit_gate_event(handle, "allowed", "")
            return answer
        log.warning("safe-to-send: BLOCKED reply for agent %s (%s)", agent_id, verdict.reason)
        self._emit_gate_event(handle, "blocked", verdict.reason)
        return verdict.safe_reply or (
            "Sorry, I'm not able to share that here. Could you give me a few more details "
            "about your own request so I can help you directly?"
        )

    def _spec(self, agent_id: str):
        """The AgentSpec for an id, or None (no registry / unknown id)."""
        if self.registry is None:
            return None
        try:
            return self.registry.get(agent_id)
        except KeyError:
            return None

    def _recent_dialog(self, agent_id: str, session_key: str, turns: int = 12) -> str:
        """The last `turns` user/assistant lines of this session, so the gate's judge can tell
        the recipient's OWN info (and whether they've identified themselves) from a real leak.
        Best-effort: any failure -> "" (the gate just judges with less context)."""
        try:
            from agent_runtime.domain.messages import AssistantMessage, UserMessage
            from agent_runtime.infrastructure.memory.local_store import SessionStore

            spec = self._spec(agent_id)
            state_dir = (
                spec.state_dir if spec is not None else getattr(self.config, "state_dir", None)
            )
            if state_dir is None:
                return ""
            msgs = SessionStore(state_dir, session_key).load()
            lines = []
            for m in msgs[-turns:]:
                if isinstance(m, UserMessage):
                    t = (m.content or "").strip()
                    if t:
                        lines.append(f"Customer: {t}")
                elif isinstance(m, AssistantMessage):
                    t = m.text.strip()
                    if t:
                        lines.append(f"Assistant: {t}")
            return "\n".join(lines)
        except Exception:  # noqa: BLE001 — context is a nice-to-have, never break the gate
            return ""

    def _emit_gate_event(self, handle: RunHandle, decision: str, reason: str) -> None:
        """Record a safe-to-send decision to the durable event log (best-effort)."""
        if self.event_log is None:
            return
        try:
            self.event_log.emit(
                handle.session_key,
                handle.run_id,
                AgentEvent("safe_to_send", {"decision": decision, "reason": reason}),
            )
        except Exception:  # noqa: BLE001
            pass

    def _last_answer(self, agent_id: str, session_key: str) -> str:
        """The agent's last assistant text in this session — the reply to send back."""
        from agent_runtime.domain.messages import AssistantMessage, TextContent
        from agent_runtime.infrastructure.memory.local_store import SessionStore

        try:
            state_dir = self.config.state_dir
            if self.registry is not None:
                try:
                    state_dir = self.registry.get(agent_id).state_dir
                except KeyError:
                    pass
            for m in reversed(SessionStore(state_dir, session_key).load()):
                if isinstance(m, AssistantMessage):
                    text = "".join(c.text for c in m.content if isinstance(c, TextContent))
                    if text.strip():
                        return text.strip()
        except Exception:  # noqa: BLE001
            log.warning("could not read channel reply for %s", session_key, exc_info=True)
        return ""

    async def _post_cron(self, task) -> bool:
        """Fire a due scheduled task as a cron-mode run (the agent executes its
        payload). Returns False if the agent's cron lane is busy, so the scheduler
        leaves the task due and retries next poll (never drops a one-shot)."""
        session_key = task.session_key
        existing = self.runs.get(session_key)
        if existing is not None and existing.task is not None and not existing.task.done():
            return False
        # deliver=message -> emit the stored text verbatim; deliver=run -> execute it
        message = (
            OUTBOX_PROMPT.format(text=task.payload)
            if getattr(task, "delivery", "run") == "message"
            else task.payload
        )
        run_id = uuid.uuid4().hex[:12]
        handle = RunHandle(
            run_id=run_id, session_key=session_key, abort=asyncio.Event(), client_id=None
        )
        if self.task_store is not None:
            handle.cron_run_id = self.task_store.record_run(task.id, task.agent_id)  # history
            handle.cron_task_id = task.id
            handle.cron_failure_alert = getattr(task, "failure_alert", 0)
        # A CRON RUN EXECUTES AS THE ACCOUNT THAT SCHEDULED IT.
        #
        # The scheduler fires from a heartbeat loop with no connection behind it, so the account
        # contextvar is empty here — and every state resolver downstream (user_state's session
        # dir, workspace, the memory partition) reads exactly that contextvar. Left unset, a
        # hosted user's scheduled run would read and write the SHARED state instead of theirs:
        # not a crash, just someone else's files, on a timer, unattended.
        #
        # Set BEFORE create_task and reset straight after: a Task snapshots the context at
        # creation, so the run keeps this account for its whole life while the heartbeat loop
        # goes back to having none.
        #
        # Only the id is known here (there is no session token to re-resolve), so budget/quota
        # fields are absent and a spend check sees "not over". Acceptable while cron is rare and
        # the ledger still records the spend; re-resolve here when hosted autonomy is offered.
        acct_token = None
        if getattr(task, "account_id", ""):
            acct_token = accounts.set_account({"account_id": task.account_id})
        try:
            handle.task = asyncio.create_task(self._run(handle, message, mode=RunMode.CRON))
        finally:
            if acct_token is not None:
                accounts.reset_account(acct_token)
        self.runs[session_key] = handle
        log.info(
            "cron fire: task %s -> run %s (%s)%s",
            task.id,
            run_id,
            session_key,
            f" account={task.account_id}" if getattr(task, "account_id", "") else "",
        )
        return True

    async def _post_heartbeat(self, agent_id: str) -> None:
        """Turn a scheduler tick into a heartbeat run — an internal 'client' posting a
        turn for `agent:<id>:heartbeat`. Flood-guarded: skip if the previous tick for
        this agent is still running."""
        session_key = f"agent:{agent_id}:heartbeat"
        existing = self.runs.get(session_key)
        if existing is not None and existing.task is not None and not existing.task.done():
            return  # previous tick still running
        run_id = uuid.uuid4().hex[:12]
        handle = RunHandle(
            run_id=run_id, session_key=session_key, abort=asyncio.Event(), client_id=None
        )
        handle.task = asyncio.create_task(
            self._run(handle, HEARTBEAT_PROMPT, mode=RunMode.HEARTBEAT)
        )
        self.runs[session_key] = handle
        log.info("heartbeat tick: agent %s (run %s)", agent_id, run_id)

    async def _discover_mcp_tools(self) -> None:
        """Connect to configured MCP servers and add their tools to the toolset,
        each wrapped in GuardedTool like every other tool. Best-effort: a failed
        connection is logged and never blocks the gateway from serving."""
        if self.mcp_provider is None:
            return
        try:
            from agent_runtime.domain.agent import apply_enablement

            raw = await self.mcp_provider.discover()
            # apply the SAME global on/off as the rest of the catalog (uniform layer-2 enablement)
            raw = apply_enablement(
                raw,
                getattr(self.config, "tools_enabled", None),
                getattr(self.config, "tools_disabled", ()),
            )
            self.service.add_tools(_guarded_with_source(raw, self.config))
            if raw:
                log.info("MCP: added %d tool(s) to the toolset", len(raw))
        except Exception as e:  # noqa: BLE001 — MCP must never block serving
            log.warning("MCP discovery failed: %s", e)

    # ------------------------------------------------------------- HTTP file serving

    def _allowed_file_roots(self) -> list[Path]:
        """The only directories the /file endpoint (and artifact detection) may read
        from: the shared workspace/state, the agents dir, and every registered agent's
        own workspace/state/definition dir. Anything outside is off-limits — the guard
        can't be walked out of with `..`/symlinks (paths are resolved before compare)."""
        roots: list[Path] = []
        for attr in ("workspace", "state_dir", "agents_dir"):
            v = getattr(self.config, attr, None)
            if v:
                roots.append(Path(v))
        # project SHARED workspaces (<state_dir>/projects/<id>/workspace) — one root covers all
        if getattr(self.config, "state_dir", None):
            roots.append(Path(self.config.state_dir) / "projects")
        if self.registry is not None:
            try:
                for aid in self.registry.list_ids():
                    spec = self.registry.get(aid)
                    for p in (
                        getattr(spec, "workspace", None),
                        getattr(spec, "state_dir", None),
                        getattr(spec, "dir", None),
                    ):
                        if p:
                            roots.append(Path(p))
            except Exception:  # noqa: BLE001 — a bad spec never blocks file serving
                pass
        return self._dedupe_resolved(roots)

    @staticmethod
    def _dedupe_resolved(roots: list[Path]) -> list[Path]:
        """Resolve + case-insensitively dedupe root paths. Resolution is also the symlink/
        `..` defence: is_under_roots compares resolved paths."""
        out: list[Path] = []
        seen: set[str] = set()
        for r in roots:
            try:
                rr = r.resolve()
            except (OSError, ValueError):
                continue
            k = str(rr).lower()
            if k not in seen:
                seen.add(k)
                out.append(rr)
        return out

    async def _http_request(self, connection: ServerConnection, request) -> HttpResponse | None:
        """websockets handshake hook: plain HTTP on the SAME port as the gateway —
        `GET /file` serves one guarded artifact file (so a client renders <img>/<video>
        straight from the daemon) and `GET /apps/<agentId>/…` serves an app agent's own
        UI (same origin as the WS ⇒ no CORS, no second server; docs/PROTOCOL.md §9).
        Returns a Response to short-circuit; None lets every other request (including
        the WebSocket upgrade) proceed to the handshake. A coroutine (websockets ≥ 13
        awaits it) because /file resolves the caller's identity via Accounts."""
        try:
            split = urlsplit(getattr(request, "path", "") or "")
            if split.path == "/healthz":
                # liveness for containers/load balancers: a real 200 (the WS root answers
                # 426, and probing any /apps/<id>/ would hardcode an agent). No auth — it
                # reveals nothing but "the process serves HTTP".
                return HttpResponse(
                    200, "OK", Headers({"Content-Type": "text/plain", "Content-Length": "2"}), b"ok"
                )
            if split.path == "/file":
                return await self._serve_file(split, getattr(request, "headers", {}))
            if split.path == "/platform/connect" or split.path == "/platform/status":
                return self._serve_platform(split, getattr(request, "headers", {}))
            if split.path == "/oauth/callback":
                return await self._serve_oauth_callback(split)
            if split.path == "/apps" or split.path.startswith("/apps/"):
                return await self._serve_app(split, getattr(request, "headers", {}))
            # Aliased app host (config.app_hosts): serve that agent's UI at "/" — but NEVER
            # short-circuit a WebSocket upgrade, or no WS could ever connect on the alias.
            headers = getattr(request, "headers", {})
            try:
                upgrade = (headers.get("Upgrade") or "").lower()
            except Exception:  # noqa: BLE001
                upgrade = ""
            if upgrade != "websocket":
                alias = self._host_alias(headers)
                if alias:
                    return await self._serve_app(urlsplit(f"/apps/{alias}{split.path}"), headers)
            return None  # not ours — fall through to the WS handshake
        except Exception:  # noqa: BLE001 — a file error must never crash the handshake path
            log.exception("http %s failed", getattr(request, "path", ""))
            return HttpResponse(500, "Internal Server Error", Headers({"Content-Length": "0"}), b"")

    async def _serve_app(self, split, headers=None) -> HttpResponse:
        """Serve one file of an app agent's UI: `/apps/<agentId>/<path>` maps to the agent's
        own `<dir>/ui/` (entry from its `[app]` declaration; docs/PROTOCOL.md §9).

        The static files need NO token — they are the app's shipped code, not user data;
        the WebSocket the page opens still requires the token (the opener put it in the
        page URL). Guards: registered app agents only, path resolved under the ui root
        (traversal-proof), extensionless paths fall back to the entry (SPA routing).

        WHICH agents exist here is caller-shaped, like every other egress door: the plain
        registry view is the shared catalogue (an HTTP request pins no account contextvar),
        and `_app_spec_for_caller` adds the ACCOUNT layers the caller may see — a coroutine
        for the same reason /file is (identity resolves via Accounts)."""

        def deny(code: int, reason: str) -> HttpResponse:
            return HttpResponse(code, reason, Headers({"Content-Length": "0"}), b"")

        parts = [p for p in split.path.split("/") if p]  # ["apps", "<id>", ...rest]
        if len(parts) < 2 or self.registry is None:
            return deny(404, "Not Found")
        agent_id = unquote(parts[1])
        try:
            spec = self.registry.get(agent_id)
        except KeyError:
            spec = await self._app_spec_for_caller(agent_id, split, headers)
        if spec is None:
            # Hosted: an unknown id may be a marketplace URL doing its job — a web-delivered
            # bundle whose first visitor just arrived. None => not this daemon's business.
            bootstrap = self._web_app_bootstrap(agent_id)
            return bootstrap if bootstrap is not None else deny(404, "Not Found")
        app = getattr(spec, "app", None)
        base = getattr(spec, "dir", None)
        if not app or base is None:
            return deny(404, "Not Found")
        base = Path(base).resolve()
        entry = (base / (app.get("entry") or "ui/index.html")).resolve()
        if not is_under_roots(entry, [base]) or not entry.is_file():
            return deny(404, "Not Found")
        # `/apps/<id>` (no trailing slash) must redirect so the page's RELATIVE asset urls
        # resolve under `/apps/<id>/…` instead of `/apps/…`.
        if len(parts) == 2 and not split.path.endswith("/"):
            q = f"?{split.query}" if split.query else ""
            return HttpResponse(
                307,
                "Temporary Redirect",
                Headers({"Location": f"/apps/{parts[1]}/{q}", "Content-Length": "0"}),
                b"",
            )
        ui_root = entry.parent
        rest = "/".join(unquote(p) for p in parts[2:])
        target = (ui_root / rest).resolve() if rest else entry
        if not is_under_roots(target, [ui_root]):
            return deny(404, "Not Found")
        if not target.is_file():
            if target.suffix:  # a missing real asset is a 404; a route path falls back
                return deny(404, "Not Found")
            target = entry  # SPA fallback: extensionless path -> the app entry
        if target == entry:
            # An entry-page open is the natural heartbeat for keeping a hosted web app current:
            # no cron, no watcher — an agent nobody visits is an agent nobody needs updated.
            self._web_app_refresh(agent_id)
        body = target.read_bytes()
        hdrs = Headers()
        hdrs["Content-Type"] = guess_mime(target)
        hdrs["Content-Length"] = str(len(body))
        hdrs["Cache-Control"] = "no-store"  # local-first: always the installed version
        return HttpResponse(200, "OK", hdrs, body)

    async def _app_spec_for_caller(self, agent_id: str, split, headers):
        """An app agent missing from the anonymous (shared) view — resolve THE CALLER and
        look in the account layer(s) that caller may see. The third egress door joins the
        doctrine of the other two (fan-out, /file): identity decides the view, the
        deployment shape only changes the values.

        NON-ENFORCING deployment (desktop; sign-in optional): /apps was always tokenless —
        "the app's shipped code, not user data" — and that statement covers every layer on
        this machine, so the human's own account-layer app serves entry AND assets with
        zero ceremony. The registry's deployment-owner view (all account layers) answers.

        SIGN-IN-ENFORCING deployment (hosted): the session on the URL names the ONE extra
        layer the caller may see; a deployment-owner credential (operator) sees them all;
        anonymous stays shared-only, so a stranger cannot probe another tenant's private
        app. (Asset requests carry no session, so hosted account-layer apps are entry-only
        until a scoped cookie exists — hosted apps reach real users through publish, which
        lands in the shared layer and serves fully.)"""
        finder = getattr(self.registry, "find_in_account_layers", None)
        if not callable(finder):
            return None  # stand-in registry (tests) — shared view was the whole world
        if not accounts.enabled():
            return finder(agent_id)
        identities = await self._http_identities(parse_qs(split.query or ""), headers or {})
        identities = identities or frozenset()
        if identities & ownership.DEPLOYMENT_OWNERS:
            return finder(agent_id)
        acct = next(iter(identities - ownership.DEPLOYMENT_OWNERS), None)
        return finder(agent_id, acct) if acct else None

    # ---------------------------------------------------------------- web delivery (hosted)

    def _web_app_bootstrap(self, agent_id: str) -> HttpResponse | None:
        """`/apps/<id>` for an agent this daemon does not have. Hosted deployments treat that as
        install-on-first-open; everywhere else it stays the 404 it always was (None).

        The sync runs as a TASK because this hook is synchronous handshake code — so the visitor
        gets an immediate self-refreshing page, and the download/verify/unpack happens on the
        loop. Whether the bundle may run here at all is decided by `sync_web_app`, which refuses
        anything whose author did not declare web delivery — a URL guess must not conscript this
        host into running arbitrary published bundles."""
        if not getattr(self.config, "hosted", False):
            return None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # no loop => nothing can be scheduled (unit tests, CLI paths)
            return None
        state = self.web_app_syncs.get(agent_id) or {}
        if state.get("task") is None:
            error = str(state.get("error") or "")
            if error and time.monotonic() - float(state.get("at") or 0.0) < WEB_SYNC_RETRY_SECONDS:
                # The error stands for a while instead of retrying per request: each retry is a
                # registry fetch, and this path is reachable by anyone with a URL bar.
                body = error.encode("utf-8")
                return HttpResponse(
                    404,
                    "Not Found",
                    Headers(
                        {
                            "Content-Type": "text/plain; charset=utf-8",
                            "Content-Length": str(len(body)),
                            "Cache-Control": "no-store",
                        }
                    ),
                    body,
                )
            self._web_app_sync(agent_id, loop)
        import html

        page = (
            "<!doctype html><meta charset='utf-8'><meta http-equiv='refresh' content='2'>"
            "<title>Setting up…</title>"
            "<body style='font-family:system-ui;display:grid;place-items:center;"
            "height:100vh;margin:0'>"
            f"<p>Setting up <b>{html.escape(agent_id)}</b>… this page refreshes itself.</p>"
        ).encode()
        return HttpResponse(
            200,
            "OK",
            Headers(
                {
                    "Content-Type": "text/html; charset=utf-8",
                    "Content-Length": str(len(page)),
                    "Cache-Control": "no-store",
                }
            ),
            page,
        )

    def _web_app_refresh(self, agent_id: str) -> None:
        """Throttled update check for an agent THIS daemon installed from the marketplace.
        Curated first-party agents are deliberately skipped — they ship with the deployment and
        are not the marketplace's to touch."""
        if not getattr(self.config, "hosted", False):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        market = self._marketplace()
        if not (hasattr(market, "has") and market.has(agent_id)):
            return
        state = self.web_app_syncs.get(agent_id) or {}
        if state.get("task") is not None:
            return
        if time.monotonic() - float(state.get("at") or 0.0) < WEB_SYNC_REFRESH_SECONDS:
            return
        self._web_app_sync(agent_id, loop)

    def _web_app_sync(self, agent_id: str, loop) -> None:
        """One in-flight sync per agent id. Outcome (success or the user-renderable error) lands
        in web_app_syncs for the next request to read."""

        async def sync() -> None:
            try:
                await self._marketplace().sync_web_app(agent_id)
                self.web_app_syncs[agent_id] = {"task": None, "error": "", "at": time.monotonic()}
            except Exception as e:  # noqa: BLE001 — the error IS the answer the next open serves
                log.warning("web app sync for %s failed: %s", agent_id, e)
                self.web_app_syncs[agent_id] = {
                    "task": None,
                    "error": str(e),
                    "at": time.monotonic(),
                }

        self.web_app_syncs[agent_id] = {
            "task": loop.create_task(sync()),
            "error": "",
            "at": time.monotonic(),
        }

    async def _http_identities(self, q: dict, headers) -> frozenset[str] | None:
        """WHO is fetching over plain HTTP — the SAME rule as the socket gate in
        _handle_conn, so /file and the WS are one door with two transports, never two doors
        with two rules. The session identifies (resolved via Accounts, `?session=` with the
        `?token=`/Authorization fallback every client already sends); the machine token
        authorises where sign-in is not enforced and carries the deployment-owner identity.
        None = unauthorized. There is no anonymous file tier: the public app tier serves
        shipped UI code, never user data."""

        def _param(name: str) -> str:
            return ((q.get(name) or [""])[0] or "").strip()

        token = _param("token")
        if not token:
            auth = ""
            try:
                auth = headers.get("Authorization") or ""
            except Exception:  # noqa: BLE001
                auth = ""
            if auth.startswith("Bearer "):
                token = auth[len("Bearer ") :].strip()
        account = await accounts.resolve(_param("session") or token)
        if account is not None:
            return ownership.callers(account.get("account_id"), self._hosted())
        if accounts.enabled():
            return None  # sign-in required — the machine token must not bypass it (WS rule)
        if self.auth_token and not hmac.compare_digest(token, self.auth_token):
            return None
        return ownership.callers(None, self._hosted())

    def _file_roots_for(self, identities: frozenset[str]) -> list[Path]:
        """The /file roots THIS caller may read — tenant isolation applied to disk.

        A deployment-owner identity (the desktop human, the hosted operator) reads the
        deployment's whole world: exactly the legacy roots. An account reads its OWN tenant
        subtree (where its runs and tools already write everything) plus the shared agent
        DEFINITION dirs — shipped code and assets, the same files /apps serves openly. A
        cross-tenant path is then simply OUTSIDE the caller's roots: unreachable by
        construction, not refused by a check somebody must remember to extend."""
        if identities & ownership.DEPLOYMENT_OWNERS:
            return self._allowed_file_roots()
        roots: list[Path] = []
        state_dir = getattr(self.config, "state_dir", None)
        if state_dir:
            roots.extend(user_state.account_root(state_dir, acct) for acct in identities)
        if self.registry is not None:
            try:
                for aid in self.registry.list_ids():
                    d = getattr(self.registry.get(aid), "dir", None)
                    if d:
                        roots.append(Path(d))
            except Exception:  # noqa: BLE001 — a bad spec never blocks file serving
                pass
        return self._dedupe_resolved(roots)

    async def _serve_file(self, split, headers) -> HttpResponse:
        """Serve one guarded file with single-range support (so <video> can seek)."""

        def deny(code: int, reason: str) -> HttpResponse:
            return HttpResponse(code, reason, Headers({"Content-Length": "0"}), b"")

        q = parse_qs(split.query)
        identities = await self._http_identities(q, headers)
        if identities is None:
            return deny(401, "Unauthorized")

        raw = unquote((q.get("path") or [""])[0])
        if not raw:
            return deny(400, "Bad Request")
        p = Path(raw)
        if not is_under_roots(p, self._file_roots_for(identities)) or not p.is_file():
            return deny(404, "Not Found")

        try:
            size = p.stat().st_size
        except OSError:
            return deny(404, "Not Found")

        # optional single byte-range (video seeking / resumable fetch)
        start, end, status, reason = 0, size - 1, 200, "OK"
        rng = ""
        try:
            rng = headers.get("Range") or ""
        except Exception:  # noqa: BLE001
            rng = ""
        if rng.startswith("bytes=") and size > 0:
            spec = rng[len("bytes=") :].split(",")[0].strip()
            lo, _, hi = spec.partition("-")
            try:
                if lo == "":
                    start, end = max(0, size - int(hi)), size - 1
                else:
                    start = int(lo)
                    end = int(hi) if hi else size - 1
                start = max(0, min(start, size - 1))
                end = max(start, min(end, size - 1))
                status, reason = 206, "Partial Content"
            except ValueError:
                start, end, status, reason = 0, size - 1, 200, "OK"

        with open(p, "rb") as f:
            f.seek(start)
            body = f.read(end - start + 1)

        hdrs = Headers()
        hdrs["Content-Type"] = guess_mime(p)
        hdrs["Content-Length"] = str(len(body))
        hdrs["Accept-Ranges"] = "bytes"
        hdrs["Cache-Control"] = "no-cache"
        # inline for media; an ASCII-safe filename helps "save as" for documents
        disp = "inline"
        if p.name.isascii() and '"' not in p.name:
            disp = f'inline; filename="{p.name}"'
        hdrs["Content-Disposition"] = disp
        if status == 206:
            hdrs["Content-Range"] = f"bytes {start}-{end}/{size}"
        return HttpResponse(status, reason, hdrs, body)

    def _platform_bearer_ok(self, q: dict, headers) -> bool:
        """Does this request carry the daemon's machine token? `?token=` or `Authorization:
        Bearer`, compared in constant time — the same rule `/file` uses."""
        tok = (q.get("token") or [""])[0]
        if not tok:
            auth = ""
            try:
                auth = headers.get("Authorization") or ""
            except Exception:  # noqa: BLE001
                auth = ""
            if auth.startswith("Bearer "):
                tok = auth[len("Bearer ") :].strip()
        return hmac.compare_digest(tok, self.auth_token)

    def _signin_hint(self) -> dict:
        """The MINIMUM an unauthenticated page needs to put a sign-in form on screen: where to
        sign in, and whether this build has a Cloud to switch to.

        `signedIn` is false by construction rather than by policy — identity is a property of a
        SOCKET here, and an HTTP handshake has no socket, so there is no session to report."""
        return {
            # The BROWSER-facing address, which on a hosted deployment is not the one this daemon
            # calls (that is internal service DNS a visitor cannot resolve).
            "accountsUrl": client_accounts_url(self.config),
            "signedIn": False,
            "email": "",
            "accountId": "",
            "canUseCloud": model_proxy.available(),
        }

    def _serve_platform(self, split, headers) -> HttpResponse:
        """Sign-in over plain HTTP on the gateway port — the RELIABLE transport for an
        app-agent page (same origin as the served UI, so `fetch` just works; no dependence
        on a WS request/response round-trip surviving the live model-proxy reconfigure
        that `platform.connect` triggers).

        `GET /platform/status`                    → current hosted-platform view (JSON).
        `GET /platform/connect?session=<sess_…>`  → bind this LOCAL install to that account
                                                     token (persists it + reconfigures live),
                                                     then return the fresh status.

        Auth mirrors `/file`: the daemon's bearer token (page URL `?token=` or Authorization)
        must match when one is set. The accounts session token rides in `?session=` — localhost
        only, never logged."""

        def send(code: int, reason: str, obj: dict) -> HttpResponse:
            body = json.dumps(obj).encode("utf-8")
            hdrs = Headers()
            hdrs["Content-Type"] = "application/json"
            hdrs["Content-Length"] = str(len(body))
            hdrs["Cache-Control"] = "no-store"
            return HttpResponse(code, reason, hdrs, body)

        q = parse_qs(split.query)
        if self.auth_token and not self._platform_bearer_ok(q, headers):
            # THE BARE MARKETPLACE LINK. A page opened at /apps/<id>/ from a store card holds no
            # machine token — nobody handed it one — and on a deployment that REQUIRES sign-in it
            # cannot open a socket either. Without an answer here the sign-in form can never
            # render, so the page reconnect-loops on 4401 forever with nothing on screen.
            #
            # Status ONLY, and only the two fields a gate reads. Not `_platform_status()`: that
            # also reports proxy internals, which an unauthenticated caller has no business
            # reading. /platform/connect stays closed — it is a state change, not a question.
            if split.path == "/platform/status" and accounts.enabled():
                return send(200, "OK", self._signin_hint())
            return send(401, "Unauthorized", {"error": "unauthorized"})

        if split.path == "/platform/status":
            return send(200, "OK", self._platform_status())

        session = (q.get("session") or [""])[0]
        try:
            status = self._platform_connect({"token": session})
        except ValueError as e:
            return send(400, "Bad Request", {"error": str(e)})
        return send(200, "OK", status)

    # --------------------------------------------------------- artifact detection

    def _enrich_artifacts(self, event: AgentEvent) -> None:
        """Lift a tool result's DECLARED artifacts to the top of the event so clients can
        render them, IN PLACE. Nothing is inferred: the artifacts are exactly what the
        producing tool handed back (ToolResultMessage.artifacts). A tool that read/searched
        /listed files declares none, so no random file can ever surface here."""
        if event.type != "tool_execution_end":
            return
        result = event.payload.get("result") or {}
        arts = result.get("artifacts") if isinstance(result, dict) else None
        if arts:
            event.payload["artifacts"] = arts

    def _presented_token(self, ws: ServerConnection) -> str:
        """The client's bearer credential: `?token=` on the connect URL (the only slot a browser
        WebSocket has) or an `Authorization: Bearer` header. Empty when neither is present."""
        request = getattr(ws, "request", None)
        if request is None:
            return ""
        query = parse_qs(urlsplit(getattr(request, "path", "") or "").query)
        presented = (query.get("token") or [""])[0]
        if not presented:
            auth_header = request.headers.get("Authorization") or ""
            if auth_header.startswith("Bearer "):
                presented = auth_header[len("Bearer ") :].strip()
        return presented

    def _presented_session(self, ws: ServerConnection) -> str:
        """The client's ACCOUNT credential — who it claims to be.

        `?session=` on the connect URL, falling back to `?token=`. The fallback keeps a hosted
        client unchanged: there the session token has always been the only credential and rides in
        `?token=`. A laptop client sends BOTH — `?token=` for the machine token that lets it
        connect, `?session=` for the person using it — and the two must not be confused, since one
        is a machine secret and the other identifies a human."""
        return self._query(ws, "session") or self._presented_token(ws)

    def _presented_mode(self, ws: ServerConnection) -> str:
        """`?mode=local|cloud` — which keys this client wants its model calls to run on.

        A preference, never a credential: the daemon pays with the session on this same
        connection, so a client can only ever bill itself. Saying `local` opts out of the proxy
        WITHOUT giving up identity — "use my own API keys" must not mean "log me out".

        Lower-cased here rather than only where it is consumed: a token must never be case-folded,
        so the shared reader cannot do it, and one caller normalising while another does not is
        how `?mode=CLOUD` ends up meaning neither thing."""
        return self._query(ws, "mode").lower()

    @staticmethod
    def _query(ws: ServerConnection, name: str) -> str:
        request = getattr(ws, "request", None)
        if request is None:
            return ""
        query = parse_qs(urlsplit(getattr(request, "path", "") or "").query)
        return (query.get(name) or [""])[0].strip()

    def _authorized(self, ws: ServerConnection) -> bool:
        """M2 auth: the client's token — `?token=` on the URL (the only slot browser
        WebSockets have) or an `Authorization: Bearer` header — must match ours."""
        if not self.auth_token:
            return True
        return hmac.compare_digest(self._presented_token(ws), self.auth_token)

    def _origin_allowed(self, ws: ServerConnection) -> bool:
        """Browser-origin gate: native/origin-less clients (terminal, Electron file pages,
        app shells) pass; an http(s) WEB PAGE must be same-host as the gateway (any port —
        dev servers and the /apps pages use their own) or loopback. A cross-host web page
        is refused so the bearer token is not the only wall against a hostile site."""
        request = getattr(ws, "request", None)
        origin = ""
        if request is not None:
            try:
                origin = request.headers.get("Origin") or ""
            except Exception:  # noqa: BLE001
                origin = ""
        if not origin or origin == "null" or "://" not in origin:
            return True  # no browser origin — a native client, not a web page
        scheme = origin.split("://", 1)[0].lower()
        if scheme not in ("http", "https"):
            return True  # file://, app://, custom shells
        try:
            host = urlsplit(origin).hostname or ""
        except ValueError:
            return False
        if host in ("localhost", "127.0.0.1", "::1"):
            return True
        served_host = ""
        try:
            served_host = urlsplit(f"//{request.headers.get('Host') or ''}").hostname or ""
        except Exception:  # noqa: BLE001
            served_host = ""
        return bool(host) and host == served_host

    def _host_alias(self, headers) -> str | None:
        """Hosted deployments: config.app_hosts maps a vanity hostname to an agent id
        ({"weather.example.com": "weather"}) so each curated agent lives at its OWN URL
        on the shared daemon. Empty map (the default, and every local install) => fully
        dormant. Pure lookup — serving/scoping callers decide what to do with the id."""
        hosts = getattr(self.config, "app_hosts", None) or {}
        if not hosts or headers is None:
            return None
        try:
            host = urlsplit(f"//{headers.get('Host') or ''}").hostname or ""
        except Exception:  # noqa: BLE001 — a malformed Host header is just "no alias"
            return None
        return hosts.get(host.lower())

    def _connection_scope(self, ws: ServerConnection) -> str | None:
        """An app connection declares `scope=agent:<id>` on its connect URL — it is then
        limited to the stable method tier, forced onto that agent, and receives only that
        agent's events. Until real auth lands this is a correctness seam, not security.
        Fallback: a connection arriving on an aliased app host (config.app_hosts) is
        scoped to that host's agent server-side — the page never has to know."""
        request = getattr(ws, "request", None)
        if request is None:
            return None
        query = parse_qs(urlsplit(getattr(request, "path", "") or "").query)
        raw = (query.get("scope") or [""])[0]
        if raw.startswith("agent:"):
            return raw[len("agent:") :].strip() or None
        return self._host_alias(getattr(request, "headers", None))

    def _public_scope_ok(self, scope: str | None) -> bool:
        """May an UNAUTHENTICATED connection proceed? Only when scoped to an agent whose
        [app] declares `public = true` — the author's opt-in, config/data-driven (core
        never names an agent). Everything else without a valid token stays refused."""
        if not scope or self.registry is None:
            return False
        try:
            spec = self.registry.get(scope)
        except KeyError:
            return False
        app = getattr(spec, "app", None) or {}
        return bool(app.get("public"))

    async def _handle_conn(self, ws: ServerConnection) -> None:
        if not self._origin_allowed(ws):
            await ws.close(code=4403, reason="forbidden origin")
            return
        # Scope is read BEFORE auth so an unauthenticated connection can be DOWNGRADED to
        # the public tier (when its agent's [app] opted in) instead of refused outright.
        scope = self._connection_scope(ws)
        public = False
        # WHO IS CONNECTING, AND ON WHOSE KEYS — both arrive with the connection, and the daemon
        # stores neither. That is what lets one daemon serve a hundred people: a hundred sockets,
        # a hundred resolved identities, no shared slot anywhere.
        #
        # ONE RULE FOR BOTH DEPLOYMENTS: a connection is allowed if it presents a valid MACHINE
        # token or a valid SESSION token, and its identity always comes from the session.
        #   * hosted — no machine token is configured, so only a session gets in; it authorises
        #     and identifies, exactly as before.
        #   * laptop — both exist. The machine token opens the socket before anyone has signed in
        #     and while offline (a session cannot be resolved without reaching the accounts
        #     service); the session, once presented, says who is there.
        #
        # The machine token must NOT authorise where sign-in is required, or it would be a bypass
        # on a hosted deployment. That is the one thing `enabled` still gates.
        account: dict | None = await accounts.resolve(self._presented_session(ws))
        # LOUD when a client SAID who it was and the answer didn't hold (`?session=` exactly —
        # the machine-token fallback is not a claim of identity). On a desktop the connection
        # still gets in on the machine token, so without this line the failure is invisible:
        # the client renders its stored session as "signed in" while every model call runs
        # anonymous-BYOK — days were lost to that ghost before this warning existed.
        if account is None and self._query(ws, "session"):
            log.warning(
                "connection presented a session that did not resolve — anonymous from here "
                "(expired/unknown token, or the accounts service is unreachable)"
            )
        authed = account is not None or (not accounts.enabled() and self._authorized(ws))
        if not authed:
            if self._public_scope_ok(scope) and len(self.client_public) < MAX_PUBLIC_CONNECTIONS:
                public = True
            else:
                await ws.close(code=4401, reason="unauthorized")
                return
        # Each connection — terminal, desktop, mobile, a channel adapter, an agent app —
        # gets a stable client id. Runs it starts are tagged with it, so when this
        # connection drops we can stop exactly that client's in-flight work.
        client_id = uuid.uuid4().hex
        self.clients.add(ws)
        if scope:
            self.client_scopes[ws] = scope
        if public:
            self.client_public.add(ws)
        # WHAT THIS SOCKET MAY OBSERVE — the receive half of tenant isolation (its twin is the
        # acting owner _send_all derives per frame). Resolved ONCE, next to the contextvar pin
        # below, so fan-out never re-answers "who is this". Anonymous public sockets hold NO
        # identities: shipped UI and tools.invoke answers reach them, tenant-owned events never.
        self.client_identities[ws] = (
            frozenset()
            if public
            else ownership.callers((account or {}).get("account_id"), self._hosted())
        )
        if account is not None:
            log.info(
                "connection %s authorized: account=%s <%s>",
                client_id[:8],
                account.get("account_id"),
                account.get("email"),
            )
        # Pin the account on the contextvar for the WHOLE connection so the read-side state
        # resolvers (_resolve_state_dir / _resolve_workspace / enumerators) route to this
        # account's subtree — the same account a run already sees. None (desktop/no-account)
        # => resolvers fall back to the shared/agent dirs, unchanged. Reset on disconnect.
        _conn_acct_tok = accounts.set_account(account)
        # WHICH KEYS this connection's model calls run on. Pinned the same way and for the same
        # reason: it is this client's choice, not the machine's, so two windows can differ.
        _conn_bill_tok = model_proxy.set_billing(self._presented_mode(ws))
        try:
            async for raw in ws:
                try:
                    frame = parse_frame(raw)
                except ProtocolError as e:
                    await ws.send(dump_frame(Response(id="", ok=False, payload={"error": str(e)})))
                    continue
                if isinstance(frame, Request):
                    # auth.update is handled HERE, not in _dispatch, because it mutates
                    # CONNECTION state (who this socket is, and which credential its model calls
                    # pay with) rather than doing anything at the application tier. Dispatch
                    # receives an immutable snapshot of the account by design; routing this
                    # through it would mean giving every method the power to change identity.
                    if frame.method == "auth.update":
                        account, response = await self._auth_update(frame, account)
                        accounts.reset_account(_conn_acct_tok)
                        _conn_acct_tok = accounts.set_account(account)
                        self.client_identities[ws] = ownership.callers(
                            (account or {}).get("account_id"), self._hosted()
                        )
                        await ws.send(dump_frame(response))
                        continue
                    response = await self._dispatch(frame, client_id, scope, public, account)
                    await ws.send(dump_frame(response))
        except websockets.ConnectionClosed:
            pass
        finally:
            accounts.reset_account(_conn_acct_tok)
            model_proxy.reset_billing(_conn_bill_tok)
            self.clients.discard(ws)
            self.client_scopes.pop(ws, None)
            self.client_public.discard(ws)
            self.client_identities.pop(ws, None)
            await self._abort_client_runs(client_id)

    async def _auth_update(self, req: Request, current: dict | None) -> tuple[dict | None, Response]:
        """Swap this connection's access token in place — no reconnect, no dropped run.

        THE PROBLEM THIS SOLVES. An access token lives ten minutes; a socket lives hours and an
        agent run can outlive the token that started it. The credential is presented once, on the
        connect URL, so without this the only way to carry a refreshed token is to rebuild the
        socket — which drops in-flight runs and re-subscribes every stream, once every ten minutes,
        forever.

        The client refreshes on its own schedule (it holds the refresh token; the daemon
        deliberately does not — see the plan) and pushes the result here.

        IDENTITY MAY NOT CHANGE. A new token for a DIFFERENT account is refused rather than
        applied: this connection's runs, subscriptions and pinned state all belong to whoever
        opened it, and silently re-pointing them at another account mid-stream would leak one
        user's events to another. Switching accounts is a reconnect.
        """
        token = str((req.params or {}).get("accessToken") or "").strip()
        if not token:
            return current, Response(
                id=req.id, ok=False, payload={"error": "accessToken is required"}
            )
        account = await accounts.resolve(token)
        if account is None:
            # The client's refresh produced something we will not take. Reported with a typed
            # code so the client signs out rather than retrying a token that cannot work.
            return current, Response(
                id=req.id,
                ok=False,
                payload={"error": "the presented token is invalid or expired", "code": "auth_invalid"},
            )
        if current and current.get("account_id") and account.get("account_id") != current.get("account_id"):
            return current, Response(
                id=req.id,
                ok=False,
                payload={
                    "error": "this token belongs to a different account; reconnect to switch",
                    "code": "auth_account_mismatch",
                },
            )
        log.info("connection re-authenticated: account=%s", account.get("account_id"))
        return account, Response(
            id=req.id,
            ok=True,
            payload={
                "accountId": account.get("account_id"),
                "expiresAt": account.get("expires_at") or 0,
            },
        )

    # --------------------------------------------------------------- dispatch

    async def _dispatch(
        self,
        req: Request,
        client_id: str | None = None,
        scope: str | None = None,
        public: bool = False,
        account: dict | None = None,
    ) -> Response:
        # PUBLIC connections (unauthenticated, [app] public opt-in): the public tier is a
        # strict subset of the scoped tier — checked FIRST, then the scoped gate applies too.
        if public and req.method not in PUBLIC_APP_METHODS:
            return Response(
                id=req.id,
                ok=False,
                payload={"error": f"method '{req.method}' is not available to public connections"},
            )
        # Agent-scoped app connections (docs/PROTOCOL.md §1/§4): stable tier only, and the
        # scoped agent is FORCED onto the params — an app can never act as another agent.
        # (Methods that take no agentId simply ignore the extra key.)
        if scope:
            if req.method not in APP_SCOPED_METHODS:
                return Response(
                    id=req.id,
                    ok=False,
                    payload={
                        "error": f"method '{req.method}' is not available to app connections"
                    },
                )
            # …unless this (agent, method) pair is an explicit CROSS_AGENT_READ, in which case
            # the caller's own agentId stands. Everything else — and every write, for everyone —
            # is forced back to self.
            if req.method not in CROSS_AGENT_READS.get(scope, ()):
                req.params["agentId"] = scope
                # The CROSS-AGENT list modes (`all` = Recents, `projectId` = project view)
                # would sidestep that forced agentId: sessions.list honors them FIRST, so a
                # third-party app page could read the account's chats with every OTHER agent
                # (found live: figure-create's panel rendering the user's JARVIS history).
                # An app sees its own agent's world, full stop.
                req.params.pop("all", None)
                req.params.pop("projectId", None)
        try:
            if req.method == "chat.send":
                payload = await self._chat_send(req.params, client_id, account)
            elif req.method == "chat.abort":
                payload = await self._chat_abort(req.params)
            elif req.method == "hello":
                payload = self._hello(req.params)
            elif req.method == "config.get":
                payload = self._config_get(scope)
            elif req.method == "config.set":
                payload = self._config_set(req.params, scope)
            elif req.method == "sessions.list":
                payload = self._sessions_list(req.params)
            elif req.method == "sessions.history":
                payload = self._sessions_history(req.params)
            elif req.method == "sessions.rename":
                payload = await self._sessions_rename(req.params)
            elif req.method == "sessions.delete":
                payload = await self._sessions_delete(req.params)
            elif req.method == "sessions.move":
                payload = await self._sessions_move(req.params)
            elif req.method == "sessions.duplicate":
                payload = await self._sessions_duplicate(req.params)
            elif req.method == "projects.list":
                payload = self._projects_list()
            elif req.method == "projects.create":
                payload = await self._projects_create(req.params)
            elif req.method == "projects.rename":
                payload = await self._projects_rename(req.params)
            elif req.method == "projects.delete":
                payload = await self._projects_delete(req.params)
            elif req.method == "projects.setLead":
                payload = await self._projects_set_lead(req.params)
            elif req.method == "projects.addMember":
                payload = await self._projects_member(req.params, add=True)
            elif req.method == "projects.removeMember":
                payload = await self._projects_member(req.params, add=False)
            elif req.method == "agents.list":
                payload = self._agents_list()
            elif req.method == "agents.detail":
                payload = self._agents_detail(req.params)
            elif req.method == "workspace.list":
                payload = self._workspace_list(req.params)
            elif req.method == "workspace.mkdir":
                payload = self._workspace_mkdir(req.params)
            elif req.method == "workspace.upload":
                payload = self._workspace_upload(req.params)
            elif req.method == "workspace.delete":
                payload = self._workspace_delete(req.params)
            elif req.method == "tools.list":
                payload = self._tools_list(req.params)
            elif req.method == "tools.invoke":
                payload = await self._tools_invoke(req.params, scope, public)
            elif req.method == "plugins.catalog":
                payload = self._plugins_catalog()
            elif req.method == "capabilities.list":
                payload = self._capabilities_list(req.params)
            elif req.method == "models.list":
                payload = self._models_list()
            elif req.method == "mcp.add":
                payload = await self._mcp_add(req.params)
            elif req.method == "oauth.connect":
                payload = await self._oauth_connect(req.params)
            elif req.method == "oauth.status":
                payload = self._oauth_status(req.params)
            elif req.method == "oauth.disconnect":
                payload = self._oauth_disconnect(req.params)
            elif req.method == "mcp.status":
                payload = self._mcp_status(req.params)
            elif req.method == "mcp.approve":
                payload = self._mcp_approve(req.params)
            elif req.method == "mcp.list":
                payload = self._mcp_list()
            elif req.method == "mcp.remove":
                payload = self._mcp_remove(req.params)
            elif req.method == "agents.create":
                payload = await self._agents_create(req.params)
            elif req.method == "agents.remove":
                payload = self._agents_remove(req.params)
            elif req.method == "cron.list":
                payload = self._cron_list()
            elif req.method == "cron.add":
                payload = self._cron_add(req.params)
            elif req.method == "cron.update":
                payload = self._cron_update(req.params)
            elif req.method == "cron.remove":
                payload = self._cron_remove(req.params)
            elif req.method == "cron.run":
                payload = self._cron_run(req.params)
            elif req.method == "cron.runs":
                payload = self._cron_runs(req.params)
            elif req.method == "notifications.list":
                payload = self._notifications_list(req.params)
            elif req.method == "notifications.ack":
                payload = self._notifications_ack(req.params)
            elif req.method == "workspace.cleanup":
                payload = self._workspace_cleanup(req.params)
            elif req.method == "marketplace.catalog":
                payload = await self._marketplace().catalog()
            elif req.method == "marketplace.installed":
                payload = self._marketplace().installed()
            elif req.method == "marketplace.install":
                payload = await self._marketplace().install(
                    bundle_id=(req.params.get("id") or "").strip(),
                    file=(req.params.get("file") or "").strip(),
                )
            elif req.method == "marketplace.uninstall":
                payload = await self._marketplace().uninstall(
                    (req.params.get("id") or "").strip(), purge_state=bool(req.params.get("purge"))
                )
            elif req.method == "platform.connect":
                payload = self._platform_connect(req.params)
            elif req.method == "platform.disconnect":
                payload = self._platform_disconnect()
            elif req.method == "platform.status":
                payload = self._platform_status()
            elif req.method in ("platform.setModelProxyUrl", "platform.setGatewayUrl"):
                payload = self._platform_set_model_proxy_url(req.params)
            else:
                return Response(
                    id=req.id, ok=False, payload={"error": f"unknown method: {req.method}"}
                )
            return Response(id=req.id, ok=True, payload=payload)
        except Exception as e:
            log.exception("dispatch error for %s", req.method)
            return Response(id=req.id, ok=False, payload={"error": f"{type(e).__name__}: {e}"})

    def _resolve_state_dir(self, agent_id: str) -> tuple[str, object]:
        """(effective agent id, its state_dir). Each agent partitions its own transcripts;
        this is the one place session RPCs map an agent to where its threads live.

        NO agent id => the default agent (main). An EXPLICIT but UNKNOWN id (a stale
        client still pointing at a deleted agent) must NOT leak the default agent's
        chats — it resolves to that id's OWN partition, which doesn't exist, so
        list/history come back EMPTY. (Bug: it used to fall back to main and show
        main's whole history under the wrong agent.)"""
        agent_id = (agent_id or "").strip() or "main"
        # HOSTED: an account's transcripts live in ITS OWN subtree, keyed by agent id — so two
        # users never see each other's threads. No account (desktop) => shared/agent dirs below.
        acct = accounts.account_id()
        if acct:
            return agent_id, user_state.account_state_dir(self.config.state_dir, acct, agent_id)
        if self.registry is None:
            return agent_id, self.config.state_dir
        try:
            return agent_id, self.registry.get(agent_id).state_dir
        except KeyError:
            if agent_id == "main":  # main should always resolve
                return "main", self.config.state_dir
            # where this agent's partition WOULD be — absent => empty, never main's
            return agent_id, Path(self.config.state_dir) / "agents" / agent_id

    def _sessions_list(self, params: dict) -> dict:
        """Saved sessions with display titles. Every row carries its `agentId` (which agent's
        partition holds the transcript) so a cross-agent client can resume the right agent.

        Three modes:
          * default (single agent) — a client passes the agent it's on and gets THAT agent's
            threads; resuming stays agent-scoped. Defaults to the default agent when none given.
          * `all: true` — merge EVERY agent's threads, newest first (powers cross-agent Recents).
          * `projectId: X` — only chats tagged with that project, across all agents (Project view).
        The cross-agent modes EXCLUDE internal agent-to-agent / cron sessions (their on-disk stem
        comes from an `agent:` key -> `agent_…`), so only human chats appear."""
        want_all = bool(params.get("all"))
        project_id = (params.get("projectId") or "").strip()
        if want_all or project_id:
            rows: list = []
            for aid, state_dir in self._agent_state_dirs():
                for s in list_sessions(state_dir):
                    if str(s.get("sessionId", "")).startswith("agent_"):
                        continue  # internal (sub-agent/cron/agent-msg): hide
                    if project_id and (s.get("projectId") or "") != project_id:
                        continue
                    rows.append({**s, "agentId": aid})
            rows.sort(key=lambda r: r.get("modified") or 0, reverse=True)
            return {"sessions": rows, "agentId": "", "all": want_all, "projectId": project_id}
        agent_id, state_dir = self._resolve_state_dir(params.get("agentId"))
        # Internal agent-to-agent / cron threads (on-disk `agent_…` stems) are never human
        # chats — hide them here too, so a per-agent session picker matches the cross-agent lists.
        rows = [
            {**s, "agentId": agent_id}
            for s in list_sessions(state_dir)
            if not str(s.get("sessionId", "")).startswith("agent_")
        ]
        return {"sessions": rows, "agentId": agent_id}

    def _sessions_history(self, params: dict) -> dict:
        """One saved session's full transcript (messages in wire form) so a client can
        RENDER a resumed conversation — the read side of `sessions.list`. Agent-scoped;
        read-only (never creates a session). The client transforms it into its own view."""
        agent_id, state_dir = self._resolve_state_dir(params.get("agentId"))
        session_key = (params.get("sessionKey") or params.get("sessionId") or "").strip()
        if not session_key:
            return {"messages": [], "sessionKey": "", "agentId": agent_id}
        from agent_runtime.infrastructure.memory.local_store import read_session_messages

        # A tool result's DECLARED artifacts are persisted in the transcript, so a resumed
        # chat replays the same deliverables a live run showed — no re-derivation needed.
        messages = [_trim_history_message(m) for m in read_session_messages(state_dir, session_key)]
        return {"messages": messages, "sessionKey": session_key, "agentId": agent_id}

    async def _sessions_rename(self, params: dict) -> dict:
        """Set a session's display title (a user rename — `manual`, so auto-titling never
        overwrites it). Agent-scoped; broadcasts sessions.changed so every client's list
        updates live. An empty title clears the manual name -> falls back to auto/snippet."""
        agent_id, state_dir = self._resolve_state_dir(params.get("agentId"))
        session_key = (params.get("sessionKey") or params.get("sessionId") or "").strip()
        if not session_key:
            return {"ok": False, "error": "sessionKey required"}
        from agent_runtime.infrastructure.memory.local_store import write_session_meta

        title = (params.get("title") or "").strip()[:80]
        write_session_meta(state_dir, session_key, title=title, manual=bool(title))
        await self._send_all(
            dump_frame(
                Event(
                    event="sessions.changed",
                    payload={"agentId": agent_id, "sessionKey": session_key},
                )
            )
        )
        return {"ok": True, "sessionKey": session_key, "title": title, "agentId": agent_id}

    async def _sessions_delete(self, params: dict) -> dict:
        """Delete a saved conversation (transcript + meta) — any client, same backend.
        Refuses while the session has an in-flight run (abort it first); broadcasts
        sessions.changed so every connected client's list updates live."""
        agent_id, state_dir = self._resolve_state_dir(params.get("agentId"))
        session_key = (params.get("sessionKey") or params.get("sessionId") or "").strip()
        if not session_key:
            return {"ok": False, "error": "sessionKey required"}
        handle = self.runs.get(session_key)
        if handle is not None and handle.task is not None and not handle.task.done():
            return {"ok": False, "error": "session has an active run — /abort it first"}
        from agent_runtime.infrastructure.memory.local_store import delete_session

        deleted = delete_session(state_dir, session_key)
        self.runs.pop(session_key, None)  # forget any finished handle
        await self._send_all(
            dump_frame(
                Event(
                    event="sessions.changed",
                    payload={"agentId": agent_id, "sessionKey": session_key, "deleted": True},
                )
            )
        )
        return {"ok": True, "deleted": deleted, "sessionKey": session_key, "agentId": agent_id}

    async def _sessions_move(self, params: dict) -> dict:
        """Assign a saved session to a project (empty projectId => back to standalone). Writes
        the session's sidecar `projectId` and broadcasts sessions.changed so every client
        re-groups it live. Agent-scoped, like the other session RPCs."""
        agent_id, state_dir = self._resolve_state_dir(params.get("agentId"))
        session_key = (params.get("sessionKey") or params.get("sessionId") or "").strip()
        if not session_key:
            return {"ok": False, "error": "sessionKey required"}
        from agent_runtime.infrastructure.memory.local_store import write_session_meta

        project_id = (params.get("projectId") or "").strip()
        write_session_meta(state_dir, session_key, projectId=project_id)
        await self._send_all(
            dump_frame(
                Event(
                    event="sessions.changed",
                    payload={"agentId": agent_id, "sessionKey": session_key},
                )
            )
        )
        return {"ok": True, "sessionKey": session_key, "projectId": project_id, "agentId": agent_id}

    async def _sessions_duplicate(self, params: dict) -> dict:
        """Copy a saved conversation (transcript + meta) into a new session with a "… (copy)"
        title, same project. Broadcasts sessions.changed so the copy appears in every client's
        list. Returns the new session key so the caller can open it."""
        agent_id, state_dir = self._resolve_state_dir(params.get("agentId"))
        session_key = (params.get("sessionKey") or params.get("sessionId") or "").strip()
        if not session_key:
            return {"ok": False, "error": "sessionKey required"}
        from agent_runtime.infrastructure.memory.local_store import duplicate_session

        new_key = duplicate_session(state_dir, session_key)
        if not new_key:
            return {"ok": False, "error": "session not found"}
        await self._send_all(
            dump_frame(
                Event(
                    event="sessions.changed",
                    payload={"agentId": agent_id, "sessionKey": new_key, "created": True},
                )
            )
        )
        return {"ok": True, "sessionKey": new_key, "agentId": agent_id}

    # ------------------------------------------------------------------ projects
    # Projects are SERVER data (one global list in the daemon's root state dir) so
    # every client shows the same folders; a session joins one via its meta sidecar.

    def _all_state_dirs(self) -> list:
        """Every place session transcripts live: the default state dir + each agent's
        partition — for project-wide session operations."""
        acct = accounts.account_id()
        if acct:  # HOSTED: only THIS account's per-agent subtrees (never another user's)
            ids = list(self.registry.list_ids()) if self.registry is not None else ["main"]
            dirs = {}
            for aid in ids:
                sd = user_state.account_state_dir(self.config.state_dir, acct, aid)
                dirs[str(sd)] = sd
            return list(dirs.values())
        dirs = {str(self.config.state_dir): self.config.state_dir}
        if self.registry is not None:
            for aid in self.registry.list_ids():
                try:
                    sd = self.registry.get(aid).state_dir
                    dirs[str(sd)] = sd
                except KeyError:
                    continue
        return list(dirs.values())

    def _agent_state_dirs(self) -> list:
        """`(agentId, state_dir)` for every partition holding transcripts — the agentId-aware
        sibling of `_all_state_dirs()`, for cross-agent session LISTING (Recents / Project view).
        Deduped by path; the daemon-root state_dir (legacy/global sessions) is tagged `main`."""
        pairs: list = []
        seen: set = set()
        acct = accounts.account_id()
        if acct:  # HOSTED: enumerate ONLY this account's per-agent subtrees
            ids = list(self.registry.list_ids()) if self.registry is not None else []
            if "main" not in ids:
                ids.append("main")
            for aid in ids:
                sd = user_state.account_state_dir(self.config.state_dir, acct, aid)
                key = str(sd)
                if key not in seen:
                    seen.add(key)
                    pairs.append((aid, sd))
            return pairs
        if self.registry is not None:
            for aid in self.registry.list_ids():
                try:
                    sd = self.registry.get(aid).state_dir
                except KeyError:
                    continue
                key = str(sd)
                if key not in seen:
                    seen.add(key)
                    pairs.append((aid, sd))
        root = str(self.config.state_dir)
        if root not in seen:  # legacy/global sessions => main
            pairs.append(("main", self.config.state_dir))
        return pairs

    def _projects_root(self):
        """Where a user's projects (+ their shared workspaces) live: the CURRENT account's root
        when signed in, else the daemon state_dir. Mirrors the per-account session/workspace
        routing (M2) so one user's projects never appear for another."""
        acct = accounts.account_id()
        if acct:
            return user_state.account_root(self.config.state_dir, acct)
        return self.config.state_dir

    def _projects_list(self) -> dict:
        from agent_runtime.infrastructure.memory import projects_store

        return {"projects": projects_store.list_projects(self._projects_root())}

    async def _projects_create(self, params: dict) -> dict:
        from agent_runtime.infrastructure.memory import projects_store

        project = projects_store.create_project(
            self._projects_root(), str(params.get("name") or "")
        )
        await self._send_all(dump_frame(Event(event="projects.changed", payload={})))
        return {"ok": True, "project": project}

    async def _projects_rename(self, params: dict) -> dict:
        from agent_runtime.infrastructure.memory import projects_store

        ok = projects_store.rename_project(
            self._projects_root(), (params.get("id") or "").strip(), str(params.get("name") or "")
        )
        if ok:
            await self._send_all(dump_frame(Event(event="projects.changed", payload={})))
        return {"ok": ok}

    async def _projects_delete(self, params: dict) -> dict:
        """Delete a project. Its chats become standalone by default; pass
        deleteSessions=true to remove them too (across every agent's partition)."""
        from agent_runtime.infrastructure.memory import projects_store
        from agent_runtime.infrastructure.memory.local_store import (
            delete_session,
            sessions_in_project,
            write_session_meta,
        )

        project_id = (params.get("id") or "").strip()
        if not project_id:
            return {"ok": False, "error": "id required"}
        removed = projects_store.delete_project(self._projects_root(), project_id)
        sessions_deleted = 0
        for state_dir in self._all_state_dirs():
            for sid in sessions_in_project(state_dir, project_id):
                if params.get("deleteSessions"):
                    delete_session(state_dir, sid)
                    sessions_deleted += 1
                else:  # untag -> standalone chat
                    write_session_meta(state_dir, sid, projectId="")
        await self._send_all(dump_frame(Event(event="projects.changed", payload={})))
        await self._send_all(dump_frame(Event(event="sessions.changed", payload={})))
        return {"ok": removed, "sessionsDeleted": sessions_deleted}

    async def _projects_set_lead(self, params: dict) -> dict:
        """Set a project's LEAD agent (Layer B): who answers when you 'message the project'.
        Empty agentId clears it. Validates the agent exists; broadcasts projects.changed."""
        from agent_runtime.infrastructure.memory import projects_store

        project_id = (params.get("id") or "").strip()
        agent_id = (params.get("agentId") or "").strip()
        if not project_id:
            return {"ok": False, "error": "id required"}
        if agent_id and self.registry is not None and agent_id not in self.registry.list_ids():
            return {"ok": False, "error": f"unknown agent: {agent_id}"}
        ok = projects_store.set_lead(self._projects_root(), project_id, agent_id)
        if ok:
            await self._send_all(dump_frame(Event(event="projects.changed", payload={})))
        return {"ok": ok, "id": project_id, "defaultAgentId": agent_id}

    async def _projects_member(self, params: dict, add: bool) -> dict:
        """Add/remove one agent on a project's curated roster (Layer B). The roster is what
        the project UI surfaces — the lead may still call ANY agent (open orchestration)."""
        from agent_runtime.infrastructure.memory import projects_store

        project_id = (params.get("id") or "").strip()
        agent_id = (params.get("agentId") or "").strip()
        if not project_id or not agent_id:
            return {"ok": False, "error": "id and agentId required"}
        if add and self.registry is not None and agent_id not in self.registry.list_ids():
            return {"ok": False, "error": f"unknown agent: {agent_id}"}
        project = projects_store.get_project(self._projects_root(), project_id)
        if project is None:
            return {"ok": False, "error": "unknown project"}
        members = list(project.get("members") or [])
        members = (members + [agent_id]) if add else [m for m in members if m != agent_id]
        ok = projects_store.set_members(self._projects_root(), project_id, members)
        if ok:
            await self._send_all(dump_frame(Event(event="projects.changed", payload={})))
        return {"ok": ok, "id": project_id, "members": members}

    def _session_project_id(self, agent_id: str, session_key: str) -> str:
        """The project a session belongs to ('' = standalone) — read from its meta sidecar in
        that agent's partition. Used to INHERIT the project onto delegated child runs."""
        if not session_key:
            return ""
        from agent_runtime.infrastructure.memory.local_store import read_session_meta

        _, state_dir = self._resolve_state_dir(agent_id)
        return (read_session_meta(state_dir, session_key).get("projectId") or "").strip()

    def _inherit_project(
        self, parent_agent: str, parent_key: str, child_agent: str, child_key: str
    ) -> None:
        """Layer B: a child run spawned from a PROJECT chat belongs to the same project — write
        the child session's meta (projectId + internal) BEFORE it runs, so its workspace binds
        to the project folder and its deliverables stay with the project. Best-effort."""
        try:
            pid = self._session_project_id(parent_agent, parent_key)
            if not pid:
                return
            from agent_runtime.infrastructure.memory.local_store import write_session_meta

            _, child_state_dir = self._resolve_state_dir(child_agent)
            write_session_meta(child_state_dir, child_key, projectId=pid, internal=True)
        except Exception:  # noqa: BLE001 — inheritance must never block the delegation itself
            log.debug("project inheritance failed for %s", child_key, exc_info=True)

    async def _maybe_generate_title(self, session_key: str, agent_id: str | None) -> None:
        """After a session's first interactive exchange, generate a short title (once) and
        store it — LM-Studio style. Skips if a title already exists (auto or user). Runs as
        a background task off the run's finally; best-effort, never affects the run."""
        try:
            from agent_runtime.application.tool_models import brain_model, resolve_tool_model
            from agent_runtime.infrastructure.memory.local_store import (
                read_session_messages,
                read_session_meta,
                write_session_meta,
            )
            from agent_runtime.infrastructure.session_titles import generate_title

            aid = (agent_id or "").strip()
            if not aid and self.registry is not None:
                try:
                    aid = self.registry.resolve(session_key).id
                except Exception:  # noqa: BLE001
                    aid = "main"
            aid, state_dir = self._resolve_state_dir(aid)
            if read_session_meta(state_dir, session_key).get("title"):
                return  # already titled — do it once
            messages = read_session_messages(state_dir, session_key)
            first_user = next((m["content"] for m in messages if m.get("role") == "user"), "")
            if not first_user:
                return
            first_assistant = ""
            for m in messages:
                if m.get("role") == "assistant":
                    first_assistant = "".join(
                        b.get("text", "") for b in m.get("content", []) if b.get("type") == "text"
                    )
                    if first_assistant:
                        break
            # title model: a config override (plugins.titles.tools.generate.model), else the
            # cheap cost-efficiency text model if set, else the agent's brain. Small call.
            ce = getattr(self.config, "cost_efficiency", None) or {}
            default_model = ce.get("text_model") or brain_model(self.config)
            model = resolve_tool_model(self.config, "titles", "generate", default=default_model)
            title = await asyncio.to_thread(generate_title, first_user, first_assistant, model)
            if title:
                write_session_meta(state_dir, session_key, title=title, auto=True)
                await self._send_all(
                    dump_frame(
                        Event(
                            event="sessions.changed",
                            payload={"agentId": aid, "sessionKey": session_key},
                        )
                    )
                )
                log.info("session '%s' titled: %r", session_key, title)
        except Exception:  # noqa: BLE001 — titling must never break anything
            log.debug("auto-title failed for %s", session_key, exc_info=True)

    async def _mcp_add(self, params: dict) -> dict:
        """Hot-add an MCP server: build the config, connect it LIVE, merge its tools into the
        catalog (no restart), and PERSIST to config.mcp_servers (writes agentd.config.json). The
        central registry for bare connections — `claude mcp add` / `openclaw mcp add` equivalent."""
        from agent_runtime.config import McpServerConfig
        from agent_runtime.domain.agent import apply_enablement

        name = (params.get("name") or "").strip()
        command = params.get("command") or None
        url = (params.get("url") or "").strip() or None
        if not name:
            return {"added": False, "error": "name required"}
        if not (command or url):
            return {"added": False, "error": "need a command (stdio) or url (http)"}
        if any(getattr(s, "name", "") == name for s in (self.config.mcp_servers or [])):
            return {"added": False, "error": f"server '{name}' already exists"}
        cfg = McpServerConfig(
            name=name,
            transport="http" if url else "stdio",
            command=command,
            url=url,
            env=params.get("env") or None,
            headers=params.get("headers") or None,
        )
        provider = self._ensure_mcp_provider()
        if provider is None:
            return {"added": False, "error": "MCP SDK not installed (pip install mcp)"}
        tools = await provider.add_server(cfg)
        if not tools:
            return {"added": False, "error": f"could not connect to '{name}'"}
        tools = apply_enablement(
            tools,
            getattr(self.config, "tools_enabled", None),
            getattr(self.config, "tools_disabled", ()),
        )
        self.service.add_tools(_guarded_with_source(tools, self.config))
        self.config.mcp_servers = list(self.config.mcp_servers or []) + [cfg]
        persisted = _persist_mcp_servers(self.config)
        log.info("mcp.add '%s' -> %d tool(s), persisted=%s", name, len(tools), persisted)
        return {
            "added": True,
            "name": name,
            "tools": [getattr(t, "name", "") for t in tools],
            "persisted": persisted,
        }

    def _ensure_mcp_provider(self):
        """The live MCP provider, building an empty one on first hot-add if none exists yet
        (no servers were configured at startup). None if the `mcp` SDK isn't installed."""
        if self.mcp_provider is not None:
            return self.mcp_provider
        try:
            import mcp  # noqa: F401

            from agent_runtime.infrastructure.tools.mcp.provider import McpProvider
            from agent_runtime.infrastructure.tools.mcp.session import create_session

            self.mcp_provider = McpProvider([], create_session)
            return self.mcp_provider
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------- marketplace (M4)

    def _marketplace(self):
        """Lazy marketplace service for the CALLING ACCOUNT: progress broadcasts to every client
        (the store UI renders them) and after_change hot-reloads agents + plugins — install to
        usable with NO restart.

        PER ACCOUNT, because an install writes files. With one shared service every install
        unpacked into the daemon-global agents_dir and recorded itself in one
        installed_bundles.json, so on a hosted daemon a visitor's install appeared in everybody's
        store as "installed" and their uninstall deleted it for everybody. The service itself is
        unchanged — it is composed against a config whose agents/plugins/state dirs point into
        the caller's own subtree (user_state), which is the same trick sessions and workspace
        already use.

        Desktop is untouched: accounts are off, account_id() is None, and there is exactly one
        service built from the unmodified config — including one that a test injected.
        """
        from agent_runtime.infrastructure.marketplace import build_marketplace_service

        acct = accounts.account_id()
        if not acct:
            if self.marketplace is None:
                self.marketplace = build_marketplace_service(
                    self.config,
                    on_event=self._marketplace_progress,
                    after_change=self._marketplace_after_change,
                )
            return self.marketplace

        service = self.account_marketplaces.get(acct)
        if service is None:
            import dataclasses

            scoped = dataclasses.replace(
                self.config,
                agents_dir=user_state.account_agents_dir(self.config.state_dir, acct),
                plugins_dir=user_state.account_plugins_dir(self.config.state_dir, acct),
                # state_dir carries installed_bundles.json and the download scratch dir, so it
                # has to move too — otherwise two accounts would share one "what is installed"
                # ledger while owning different files, and the store would show the union.
                state_dir=user_state.account_root(self.config.state_dir, acct),
            )
            service = build_marketplace_service(
                scoped,
                on_event=self._marketplace_progress,
                after_change=self._marketplace_after_change,
            )
            self.account_marketplaces[acct] = service
        return service

    def _marketplace_progress(self, payload: dict) -> None:
        """Sync -> async bridge for install progress (the service is transport-blind)."""
        try:
            asyncio.get_running_loop().create_task(
                self._send_all(dump_frame(Event(event="marketplace.progress", payload=payload)))
            )
        except RuntimeError:  # no loop (CLI offline path) — progress goes nowhere, fine
            pass

    def _marketplace_after_change(self, changed: dict | None = None) -> dict:
        """Post-install/uninstall: re-scan agents, hot-load any NEW plugins' tools, and
        tell every client the agent list changed (switchers refresh live)."""
        # An acquired addon JOINS the provisioning set (tiers doc §3) — extend the
        # in-memory profile BEFORE re-discovery, or a Studio flavor would gate out the
        # plugins it just installed. (load_config unions the ledger on every start.)
        new_plugins = tuple((changed or {}).get("plugins") or ())
        profile = getattr(self.config, "distribution", None)
        if new_plugins and profile is not None and profile.provisioned_plugins is not None:
            import dataclasses

            merged = tuple(dict.fromkeys(profile.provisioned_plugins + new_plugins))
            self.config.distribution = dataclasses.replace(profile, provisioned_plugins=merged)
        agents: list = []
        if self.registry is not None and hasattr(self.registry, "refresh"):
            agents = self.registry.refresh()
        tools: list = []
        reloader = getattr(self.service, "plugin_reloader", None)
        if callable(reloader):
            tools = (reloader() or {}).get("tools", [])
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                self._send_all(
                    dump_frame(Event(event="agents.changed", payload=self._agents_list()))
                )
            )
            # a freshly installed agent gets its tagline/suggestions generated too
            loop.create_task(self._maybe_generate_presentations())
        except RuntimeError:
            pass
        return {"agents": agents, "tools": tools}

    def _mcp_list(self) -> dict:
        """Every MCP server — config-registered AND plugin-MCP — with whether its tools are live.
        The unified 'what MCP do I have' surface (matches /tools for tools)."""
        loaded = {
            info["name"].split("__", 1)[0]
            for info in self.service.list_tools()
            if "__" in info.get("name", "")
        }
        servers = [
            {
                "name": getattr(s, "name", ""),
                "transport": getattr(s, "transport", "stdio"),
                "command": getattr(s, "command", None),
                "url": getattr(s, "url", None),
                "connected": getattr(s, "name", "") in loaded,
            }
            for s in (self.config.mcp_servers or [])
        ]
        return {"servers": servers, "count": len(servers)}

    def _mcp_remove(self, params: dict) -> dict:
        """Remove an MCP server: drop it from config (persisted) + drop its live tools."""
        name = (params.get("name") or "").strip()
        if not name:
            return {"removed": False, "error": "name required"}
        servers = [s for s in (self.config.mcp_servers or []) if getattr(s, "name", "") != name]
        if len(servers) == len(self.config.mcp_servers or []):
            return {"removed": False, "error": f"no such server: {name}"}
        self.config.mcp_servers = servers
        dropped = self.service.remove_tools(f"{name}__")
        _persist_mcp_servers(self.config)
        return {"removed": True, "name": name, "toolsDropped": dropped}

    def _tools_list(self, params: dict) -> dict:
        """The live tool catalog — the uniform 'what tools exist' surface any client can render.
        No agentId => the full active catalog; with agentId => the subset THAT agent sees in an
        interactive turn (its allow/deny scope), i.e. what the model would be handed."""
        agent_id = (params.get("agentId") or "").strip() or None
        tools = self.service.list_tools(agent_id)
        return {"tools": tools, "count": len(tools), "agentId": agent_id}

    def _resolved_skills(self, agent_id: str | None):
        """Skills for a capabilities query: the shared library (no agent), else that agent's set
        (shared library + its own, deduped, own wins) — the same resolution agents.detail uses."""
        from agent_runtime.infrastructure.skills.file_skills import load_skills_dir

        if self.registry is None:
            return []
        try:
            main_dir = self.registry.get("main").skills_dir
        except KeyError:
            return []
        aid = (agent_id or "").strip()
        if not aid:
            return load_skills_dir(main_dir)
        try:
            own_dir = getattr(self.registry.get(aid), "skills_dir", None)
        except KeyError:
            own_dir = None
        by_name: dict = {}
        for sd in (main_dir, own_dir):
            if sd:
                for sk in load_skills_dir(sd):
                    by_name[sk.name] = sk
        return list(by_name.values())

    def _tool_catalog(self, agent_id: str | None = None) -> dict:
        """The plugin -> tool catalog behind the capability / model / settings views. The UNION of
        (a) COLD discovery — every installed plugin, including tools switched off via ``tools_disabled``
        that the settings page must still show to re-enable, and (b) the LIVE registered toolset
        (``service.catalog_tools``) — the DI-gated tools (browser/computer, the autonomy ledger) that
        cold discovery can't see because it injects no runtime handles. That missing-half is the bug
        this fixes. Falls back to cold-only when there's no service yet."""
        from agent_runtime.infrastructure.plugins.catalog import (
            build_catalog,
            catalog_from_tools,
            merge_catalogs,
        )

        try:
            cold = build_catalog(self.config)
        except Exception as e:  # noqa: BLE001 — a bad plugin never sinks the whole catalog
            log.warning("tool catalog: cold discovery failed: %s", e)
            cold = {}
        svc = getattr(self, "service", None)
        if svc is None or not hasattr(svc, "catalog_tools"):
            return cold
        live = catalog_from_tools(self.config, svc.catalog_tools(agent_id))
        return merge_catalogs(cold, live)

    def _capabilities_list(self, params: dict) -> dict:
        """The UNIFORM capability catalog — tools, plugins, skills, agents in ONE shape
        (CapabilityDescriptor). Core resolves every description once and exposes them equally to
        every client; the client just renders. Optional `kind` filters to one type; optional
        `agentId` scopes the skills to that agent's resolved set (default: the shared library)."""
        from agent_runtime.application import capabilities as cap

        kind = (params.get("kind") or "").strip().lower()
        rows: list = []
        if self.registry is not None:
            specs = []
            for aid in self.registry.list_ids():
                try:
                    specs.append(self.registry.get(aid))
                except KeyError:
                    continue
            rows += cap.agent_descriptors(specs)
        try:
            catalog = self._tool_catalog()
            rows += cap.plugin_descriptors(catalog)
            rows += cap.tool_descriptors(catalog)
        except Exception as e:  # noqa: BLE001 — a bad plugin must never break the catalog
            log.warning("capabilities.list: catalog build failed: %s", e)
        rows += cap.skill_descriptors(self._resolved_skills(params.get("agentId")))
        out = [d.as_dict() for d in rows if not kind or d.kind == kind]
        return {"capabilities": out, "kind": kind, "count": len(out)}

    def _models_list(self) -> dict:
        """The ONE model overview — every model the runtime uses, in one place: the brain (and,
        when cost-efficiency is on, its two split brains) plus every model-bearing tool, each with
        what it RESOLVES to, its KIND (text/vision/image/embedding), where it's set (`configKey`,
        the dotted path config.set writes), and the source. Plus the per-kind option lists so a
        client shows the RIGHT picker. No resolution logic here — it reads the same resolvers the
        runtime uses, so this view can never drift from what actually runs."""
        cfg = self.config
        ce = getattr(cfg, "cost_efficiency", None) or {}
        ce_on = bool(isinstance(ce, dict) and ce.get("enabled"))
        brain = getattr(cfg, "model", None)
        rows: list = []
        if ce_on:
            rows.append(
                {
                    "id": "brain-text",
                    "label": "Brain · text turns",
                    "kind": "text",
                    "resolved": ce.get("text_model") or brain,
                    "configKey": "cost_efficiency.text_model",
                    "source": "cost_efficiency",
                    "note": "cost-efficiency ON",
                }
            )
            rows.append(
                {
                    "id": "brain-vision",
                    "label": "Brain · image turns",
                    "kind": "vision",
                    "resolved": ce.get("vision_model") or brain,
                    "configKey": "cost_efficiency.vision_model",
                    "source": "cost_efficiency",
                    "note": "cost-efficiency ON",
                }
            )
        else:
            rows.append(
                {
                    "id": "brain",
                    "label": "Brain (reasoning)",
                    "kind": "text",
                    "resolved": brain,
                    "configKey": "model",
                    "source": "config.model",
                }
            )
        cfg_plugins = getattr(cfg, "plugins", None) or {}
        try:
            cat = self._tool_catalog()
        except Exception as e:  # noqa: BLE001 — a bad plugin never breaks the overview
            log.warning("models.list: catalog build failed: %s", e)
            cat = {}
        for pid in sorted(cat):
            for t in cat[pid]["tools"]:
                if not t.get("needs_model"):
                    continue
                tconf = ((cfg_plugins.get(pid) or {}).get("tools") or {}).get(t["name"]) or {}
                rows.append(
                    {
                        "id": f"{pid}.{t['name']}",
                        "label": t["name"],
                        "kind": t.get("model_kind", "text"),
                        "resolved": t.get("model"),
                        "plugin": pid,
                        "configKey": f"plugins.{pid}.tools.{t['name']}.model",
                        "source": "config" if tconf.get("model") else "tool default",
                    }
                )
        return {
            "models": rows,
            "catalogs": _kind_catalogs(cfg),
            "costEfficiency": ce_on,
            "effectiveModel": _effective_model(cfg),
        }

    def _plugins_catalog(self) -> dict:
        """The plugin -> tool catalog for the settings UI: every plugin, its tools, each tool's
        on/off state, whether it takes a model (and the model it resolves to today), and its
        provider if one is configured. Reuses the same discovery + model resolver the CLI's
        list_plugins uses, then overlays enable state from config (plugin gate + tools_disabled)."""
        from agent_runtime.application.tool_models import resolve_tool_provider

        cfg = self.config
        cfg_plugins = getattr(cfg, "plugins", None) or {}
        disabled = set(getattr(cfg, "tools_disabled", None) or [])

        def plugin_enabled(pid: str) -> bool:
            v = cfg_plugins.get(pid)
            if isinstance(v, bool):
                return v
            if isinstance(v, dict):
                return v.get("enabled", True) is not False
            return True

        try:
            cat = self._tool_catalog()
        except Exception as e:  # noqa: BLE001 — never let discovery crash the settings page
            log.warning("plugins.catalog: build failed: %s", e)
            return {"plugins": [], "error": str(e)}

        out = []
        for pid in sorted(cat):
            p = cat[pid]
            tools = []
            for t in sorted(p["tools"], key=lambda x: x["name"]):
                name = t["name"]
                tools.append(
                    {
                        # UIs show the FULL canonical description; fall back to the short one
                        "description": t.get("full_description") or t.get("description", ""),
                        "name": name,
                        "needsModel": bool(t.get("needs_model")),
                        "modelKind": t.get(
                            "model_kind", "text"
                        ),  # which picker to show (text/vision/image/embedding)
                        "model": t.get("model"),
                        "provider": resolve_tool_provider(cfg, pid, name),
                        # self-described provider options so the client renders a picker (dropdown / ordered
                        # chain), never a free-text box; empty => open-ended provider => free text.
                        "providerOptions": t.get("provider_options") or [],
                        "providerChain": bool(t.get("provider_chain")),
                        # self-declared canvas action (e.g. Convert to Vector on PNGs); null => none
                        "artifactAction": t.get("artifact_action") or None,
                        "enabled": name not in disabled,
                    }
                )
            out.append(
                {
                    "id": pid,
                    "description": p.get("description", ""),
                    "enabled": plugin_enabled(pid),
                    "tools": tools,
                }
            )
        # MCP servers (plugin-declared like google + runtime-added) shown as cards too, so the page
        # reflects EVERY capability, not just native plugins. Removable/addable via mcp.* RPCs.
        out.extend(self._mcp_plugin_cards(disabled))
        return {"plugins": out}

    def _mcp_plugin_cards(self, disabled: set) -> list:
        """Every MCP server as a plugin-style card: its live tools (server__tool), whether it's
        connected, and its endpoint. Union of config.mcp_servers (so a not-yet-connected server
        still shows) with the live MCP tools grouped by their `server__` prefix."""
        from agent_runtime.infrastructure.plugins.catalog import _unwrap_tool

        svc = getattr(self, "service", None)
        by_server: dict = {}
        if svc is not None and hasattr(svc, "catalog_tools"):
            try:
                for raw in svc.catalog_tools():
                    tool = _unwrap_tool(raw)
                    name = getattr(tool, "name", "") or ""
                    pid = getattr(tool, "_plugin_id", "") or getattr(tool, "plugin", "")
                    if pid or "__" not in name:  # native tool, or not an MCP server__tool
                        continue
                    by_server.setdefault(name.split("__", 1)[0], []).append(tool)
            except Exception as e:  # noqa: BLE001 — a bad MCP tool never breaks the page
                log.warning("plugins.catalog: MCP grouping failed: %s", e)
        servers = {
            getattr(s, "name", ""): s for s in (getattr(self.config, "mcp_servers", None) or [])
        }
        cards = []
        for name in sorted(set(servers) | set(by_server)):
            s = servers.get(name)
            live = sorted(by_server.get(name, []), key=lambda t: getattr(t, "name", ""))
            endpoint = ""
            if s is not None:
                endpoint = getattr(s, "url", None) or " ".join(getattr(s, "command", None) or [])
            cards.append(
                {
                    "id": name,
                    "mcp": True,  # UI renders these as a data-source card
                    "transport": getattr(s, "transport", "stdio") if s is not None else "stdio",
                    "endpoint": endpoint,
                    "description": endpoint or "MCP server",
                    "enabled": bool(live),  # connected iff its tools are live
                    "tools": [
                        {
                            "name": getattr(t, "name", ""),
                            "label": getattr(t, "name", "").split("__", 1)[-1],
                            "description": (getattr(t, "description", "") or "").strip(),
                            "needsModel": False,
                            "modelKind": "text",
                            "model": None,
                            "provider": None,
                            "providerOptions": [],
                            "providerChain": False,
                            "enabled": getattr(t, "name", "") not in disabled,
                        }
                        for t in live
                    ],
                }
            )
        return cards

    @staticmethod
    def _agent_app(aid: str, spec) -> dict | None:
        """An agent's app surface for discovery (docs/PROTOCOL.md §9): a declared `[app]`
        whose entry file actually EXISTS ⇒ `{title, url}` — a broken/missing UI never
        advertises. The url is the path only; the OPENER appends its own token + scope."""
        app = getattr(spec, "app", None)
        base = getattr(spec, "dir", None)
        if not app or base is None:
            return None
        entry = Path(base) / (app.get("entry") or "ui/index.html")
        if not entry.is_file():
            return None
        return {
            "title": app.get("title") or getattr(spec, "name", aid),
            "url": f"/apps/{aid}/",
            # the author's declared presentation — a normal "browser" tab or the app's
            # own chromeless "window"; every opener (CLI, desktop button) honors it
            "mode": app.get("mode") or "browser",
        }

    def _agents_list(self) -> dict:
        """The available agents — the uniform discovery surface any client uses. The
        registry is the single source of truth; the session-key format stays internal.
        Includes each agent's display presentation (tagline + starter suggestions) so
        no client ever hardcodes what an agent 'is'."""
        default = getattr(self.config, "agent_id", "main")
        if self.registry is None:
            return {"agents": [{"id": default, "name": self.config.agent_name}], "default": default}
        # Ownership, rendered from data so no client invents its own guess (the guesses
        # disagreed: the sidebar said an agent existed while publish said it didn't).
        # `mine`: is the caller among the identities that own it — theirs to change.
        # `origin`: how it got here (authored | installed | curated) — an installed agent is
        # theirs to USE but not to republish. Both default open when the registry cannot say:
        # a single-user registry has no layers, and there everything genuinely is the caller's.
        owns = getattr(self.registry, "owns", None)
        origin_of = getattr(self.registry, "origin_of", None)
        # `listed` is the registry's sidebar rule (web-app copies are resolvable, not listed);
        # asked per row so this surface can never grow its own variant of the policy.
        listed = getattr(self.registry, "listed", None)
        agents = []
        for aid in self.registry.list_ids():
            if callable(listed) and not listed(aid):
                continue
            spec = self.registry.get(aid)
            agents.append(
                {
                    "id": aid,
                    "name": spec.name,
                    "version": getattr(spec, "version", "1"),
                    "tagline": getattr(spec, "tagline", ""),
                    "suggestions": list(getattr(spec, "suggestions", ()) or ()),
                    "color": getattr(spec, "color", ""),
                    "app": self._agent_app(aid, spec),
                    "mine": bool(owns(aid)) if callable(owns) else True,
                    "origin": str(origin_of(aid)) if callable(origin_of) else "authored",
                }
            )
        return {
            "agents": agents,
            "default": default if default in {a["id"] for a in agents} else "main",
        }

    def _agents_detail(self, params: dict) -> dict:
        """Everything the Agent DETAIL page shows for ONE agent: identity + a listing of its
        workspace files + its skills (shared library + the agent's own). The agent's CHATS are
        fetched separately via `sessions.list {agentId}`. Read-only; best-effort (a missing dir
        just yields an empty list)."""
        agent_id = (params.get("agentId") or "").strip() or "main"
        if self.registry is None:
            # never surface the internal id "main" as a display name
            name = self.config.agent_name if agent_id == "main" else agent_id
            return {"id": agent_id, "name": name, "workspaceFiles": [], "skills": []}
        try:
            spec = self.registry.get(agent_id)
        except KeyError:
            return {
                "id": agent_id,
                "name": agent_id,
                "workspaceFiles": [],
                "skills": [],
                "error": "unknown agent",
            }

        # --- workspace files (top level, non-recursive, newest first) ---
        from agent_runtime.infrastructure.files import classify

        files: list = []
        ws = Path(getattr(spec, "workspace", "") or "")
        try:
            entries = (
                sorted(ws.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
                if ws.is_dir()
                else []
            )
        except OSError:
            entries = []
        for p in entries[:200]:
            try:
                st = p.stat()
            except OSError:
                continue
            if p.is_dir():
                files.append({"name": p.name, "kind": "folder", "size": 0, "modified": st.st_mtime})
            else:
                cls = classify(p)
                files.append(
                    {
                        "name": p.name,
                        "kind": cls[0] if cls else "file",
                        "size": st.st_size,
                        "modified": st.st_mtime,
                    }
                )

        # --- skills: shared library (main) + the agent's own, deduped by name (own wins).
        # Each is tagged `source`: "shared" = inherited from the default library, "own" = defined
        # for THIS agent (agents/<id>/skills/). The own pass runs last, so an override lands as
        # "own". (For main itself, own dir == the shared library, so all resolve to "own".) ---
        from agent_runtime.infrastructure.skills.file_skills import load_skills_dir

        skills_by_name: dict = {}
        try:
            main_skills_dir = self.registry.get("main").skills_dir
        except KeyError:
            main_skills_dir = None
        for sd, source in ((main_skills_dir, "shared"), (getattr(spec, "skills_dir", None), "own")):
            if not sd:
                continue
            for sk in load_skills_dir(sd):
                # path included so a client can OPEN the SKILL.md (canvas view/edit)
                skills_by_name[sk.name] = {
                    "name": sk.name,
                    "description": sk.description,
                    "path": sk.path,
                    "source": source,
                }

        return {
            "id": spec.id,
            "name": spec.name,
            "description": getattr(spec, "description", ""),
            "tagline": getattr(spec, "tagline", ""),
            "version": getattr(spec, "version", "1"),
            "model": getattr(spec, "model", None) or "",
            "color": getattr(spec, "color", ""),
            "workspace": str(ws),
            "workspaceFiles": files,
            "skills": list(skills_by_name.values()),
            "app": self._agent_app(agent_id, spec),
        }

    # ------------------------------------------------------------- workspace browsing
    # The Workspace tab on an entity page (agent OR project): lazy per-directory listing
    # + user file ops. Everything resolves under ONE root (the agent's workspace or the
    # project's shared workspace) and every path is containment-checked against it, so
    # the RPCs can't be walked outside with `..`.

    def _workspace_root(self, params: dict):
        """(root Path, error) — the workspace root these ops act on: projectId wins, else
        agentId (default main). None root + a message when the entity doesn't exist.

        ``root="definition"`` switches from the agent's WORKSPACE (its output, user data) to
        its DEFINITION directory — agent.toml, IDENTITY.md, skills/, plugins/, ui/. Those are
        the files that appear while an agent is being BUILT, and they live one level ABOVE the
        workspace, so no amount of browsing the workspace ever reaches them. Read-only in
        practice: the mutating ops (mkdir/upload/delete) all resolve through here too, but
        only Agent Builder can point them at another agent, and CROSS_AGENT_READS grants it
        no mutating method."""
        project_id = (params.get("projectId") or "").strip()
        if project_id:
            from agent_runtime.infrastructure.memory import projects_store

            if projects_store.get_project(self._projects_root(), project_id) is None:
                return None, "unknown project"
            return projects_store.project_workspace_dir(self._projects_root(), project_id), ""
        agent_id = (params.get("agentId") or "").strip() or "main"
        if (params.get("root") or "").strip() == "definition":
            if self.registry is None:
                return None, "no agent registry"
            try:
                agent_dir = getattr(self.registry.get(agent_id), "dir", None)
            except KeyError:
                return None, f"unknown agent: {agent_id}"
            if not agent_dir:
                return None, f"agent '{agent_id}' has no directory on disk"
            return Path(agent_dir), ""
        acct = accounts.account_id()
        if acct:  # HOSTED: browse THIS account's own per-agent workspace
            return user_state.account_workspace(self.config.state_dir, acct, agent_id), ""
        if self.registry is not None:
            try:
                return Path(self.registry.get(agent_id).workspace), ""
            except KeyError:
                return None, f"unknown agent: {agent_id}"
        return Path(self.config.workspace), ""

    @staticmethod
    def _ws_resolve(root: Path, rel: str):
        """Resolve a RELATIVE path inside root (None if it escapes — traversal guard)."""
        p = (Path(root) / (rel or "")).resolve()
        try:
            p.relative_to(Path(root).resolve())
        except (ValueError, OSError):
            return None
        return p

    # Never shown when browsing an agent's DEFINITION: `workspace/` is the other root and has
    # its own listing, and the caches are noise nobody authored.
    DEFINITION_HIDDEN = frozenset({"workspace", "__pycache__", ".git", ".pytest_cache"})

    def _workspace_list(self, params: dict) -> dict:
        """One directory's entries (lazy tree node): dirs first, then files, name-sorted.
        Each entry: {name, kind(folder|image|video|audio|file), size, modified, rel, path}.
        `path` is absolute (for /file + canvas), `rel` drives further ops/expansion."""
        from agent_runtime.infrastructure.files import classify

        root, err = self._workspace_root(params)
        if root is None:
            return {"entries": [], "error": err}
        rel = (params.get("path") or "").strip()
        d = self._ws_resolve(root, rel)
        if d is None or not d.is_dir():
            return {"entries": [], "error": "not a directory"}
        hidden = (
            self.DEFINITION_HIDDEN
            if (params.get("root") or "").strip() == "definition"
            else frozenset()
        )
        dirs: list = []
        files: list = []
        try:
            children = sorted(d.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            children = []
        for p in children[:500]:
            if p.name in hidden:
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            crel = f"{rel}/{p.name}" if rel else p.name
            if p.is_dir():
                dirs.append(
                    {
                        "name": p.name,
                        "kind": "folder",
                        "size": 0,
                        "modified": st.st_mtime,
                        "rel": crel,
                        "path": str(p),
                    }
                )
            else:
                cls = classify(p)
                files.append(
                    {
                        "name": p.name,
                        "kind": cls[0] if cls else "file",
                        "size": st.st_size,
                        "modified": st.st_mtime,
                        "rel": crel,
                        "path": str(p),
                    }
                )
        return {"entries": dirs + files, "path": rel, "root": str(root)}

    def _workspace_mkdir(self, params: dict) -> dict:
        root, err = self._workspace_root(params)
        if root is None:
            return {"ok": False, "error": err}
        rel = (params.get("path") or "").strip().strip("/")
        if not rel:
            return {"ok": False, "error": "path required"}
        p = self._ws_resolve(root, rel)
        if p is None:
            return {"ok": False, "error": "invalid path"}
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "rel": rel}

    def _workspace_upload(self, params: dict) -> dict:
        """Save ONE user-chosen file into the workspace dir `path` (default the root),
        keeping its real name (deduped with ' (n)' on collision — never silently overwrite)."""
        import base64
        import binascii

        from agent_runtime.infrastructure.files import _safe_name

        root, err = self._workspace_root(params)
        if root is None:
            return {"ok": False, "error": err}
        b64 = params.get("dataBase64") or ""
        name = _safe_name(params.get("name") or "file")
        if not b64 or (len(b64) * 3) // 4 > UPLOAD_MAX_BYTES:
            return {"ok": False, "error": f"empty or over {UPLOAD_MAX_BYTES // (1024 * 1024)} MB"}
        d = self._ws_resolve(root, (params.get("path") or "").strip())
        if d is None:
            return {"ok": False, "error": "invalid path"}
        try:
            d.mkdir(parents=True, exist_ok=True)
            raw = base64.b64decode(b64, validate=True)
            target = d / name
            stem, suffix = target.stem, target.suffix
            n = 2
            while target.exists():  # dedupe: report.png -> report (2).png
                target = d / f"{stem} ({n}){suffix}"
                n += 1
            target.write_bytes(raw)
        except (OSError, binascii.Error, ValueError) as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "name": target.name, "path": str(target)}

    def _workspace_delete(self, params: dict) -> dict:
        """Delete ONE file or folder (recursive) inside the workspace. The root itself is
        refused, so 'delete' can never empty an agent's/project's whole workspace by accident."""
        import shutil

        root, err = self._workspace_root(params)
        if root is None:
            return {"ok": False, "error": err}
        rel = (params.get("path") or "").strip().strip("/")
        if not rel:
            return {"ok": False, "error": "path required"}
        p = self._ws_resolve(root, rel)
        if p is None or p == Path(root).resolve():
            return {"ok": False, "error": "invalid path"}
        try:
            if p.is_dir():
                shutil.rmtree(p)
            elif p.exists():
                p.unlink()
            else:
                return {"ok": False, "error": "not found"}
        except OSError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "rel": rel}

    async def _maybe_generate_presentations(self) -> None:
        """Fill in missing agent display presentation, persisted per agent in a sidecar
        (authored agent.toml fields always win), then agents.changed tells every client.
        Two independent concerns, both best-effort and never blocking:
          1. COLOUR — pure/cheap, assigned to EVERY agent (unique across the set, so it
             needs the whole registry; that's why it's server-side, not per-client).
          2. TAGLINE + suggestions — one LLM call over the agent's identity, only for
             agents that actually have an identity to describe."""
        if self.registry is None:
            return
        try:
            from agent_runtime.infrastructure.agents import presentation as pres

            changed = False
            # --- 1. colours: unique, unconditional (even identity-less agents) ---------
            taken: list[float] = []
            missing: list = []
            for aid in self.registry.list_ids():
                try:
                    spec = self.registry.get(aid)
                except KeyError:
                    continue
                if getattr(spec, "dir", None) is None:
                    continue  # nowhere to persist (synthesized main)
                if getattr(spec, "color", ""):
                    hue = pres.hex_to_hue(spec.color)
                    if hue is not None:
                        taken.append(hue)  # an assigned/authored colour is 'taken'
                else:
                    missing.append(spec)
            for spec in missing:
                hue = pres.assign_hue(spec.id, taken)
                taken.append(hue)
                pres.update_sidecar(spec.dir, color=pres.hsl_to_hex(hue), hue=round(hue, 1))
                changed = True
                log.info("agent '%s' coloured: %s", spec.id, pres.hsl_to_hex(hue))

            # --- 2. taglines + suggestions: LLM, identity-bearing agents only ----------
            from agent_runtime.application.tool_models import brain_model, resolve_tool_model

            for aid in self.registry.list_ids():
                try:
                    spec = self.registry.get(aid)
                except KeyError:
                    continue
                if getattr(spec, "tagline", "") or getattr(spec, "dir", None) is None:
                    continue
                ce = getattr(self.config, "cost_efficiency", None) or {}
                default_model = ce.get("text_model") or brain_model(self.config)
                model = resolve_tool_model(
                    self.config, "agents", "presentation", default=default_model
                )
                data = await asyncio.to_thread(
                    pres.generate_presentation,
                    spec.name,
                    getattr(spec, "description", ""),
                    spec.instructions,
                    model,
                )
                if not data:
                    continue
                pres.update_sidecar(spec.dir, **data)
                changed = True
                log.info("agent '%s' presented: %r", aid, data.get("tagline"))

            if changed:
                self.registry.refresh()  # sidecars -> live specs
                await self._send_all(
                    dump_frame(Event(event="agents.changed", payload=self._agents_list()))
                )
        except Exception:  # noqa: BLE001 — presentation is décor, never breaks serving
            log.debug("agent presentation generation failed", exc_info=True)

    def _agents_remove(self, params: dict) -> dict:
        """Permanently delete an agent EVERYWHERE — the one destructive surface any client
        uses. Purges the shared ledgers (memory + cron/goals/runs/notifs/commitments) first
        so nothing can fire orphaned, then deletes the definition + workspace + sessions and
        forgets it (no restart). Refuses 'main'."""
        agent_id = (params.get("agentId") or "").strip().lower()
        if not agent_id:
            return {"removed": False, "error": "agentId required"}
        if agent_id == "main":
            return {"removed": False, "error": "cannot delete the default agent 'main'"}
        if self.registry is None:
            return {"removed": False, "error": "no agent registry"}
        if agent_id not in self.registry.list_ids():
            return {"removed": False, "error": f"unknown agent: {agent_id}"}

        cron = self.task_store.purge_agent(agent_id) if self.task_store is not None else {}
        memory = self.memory_bank.purge_agent(agent_id) if self.memory_bank is not None else 0
        removed = self.registry.remove(
            agent_id
        )  # definition + workspace + sessions, drop from cache
        log.info("agents.remove %s -> %s cron=%s memory=%s", agent_id, removed, cron, memory)
        return {
            "removed": True,
            "agentId": agent_id,
            "definition": removed.get("definition", False),
            "sessions": removed.get("sessions", False),
            "cron": cron,
            "memory": memory,
        }

    async def _agents_create(self, params: dict) -> dict:
        """Create a new agent from a client (the 'Create agent' button) — the uniform
        authoring surface. Scaffolds agents/<id>/ (agent.toml + optional IDENTITY.md),
        loads it live (no restart), then kicks the presentation pass to give it a unique
        colour + tagline. Broadcasts agents.changed so every client shows it immediately."""
        if self.registry is None or not hasattr(self.registry, "create"):
            return {"created": False, "error": "no agent registry"}
        agent_id = (params.get("agentId") or params.get("id") or "").strip().lower()
        name = str(params.get("name") or "").strip()
        if not agent_id:
            # derive a slug from the name when the client didn't supply an id
            agent_id = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        if not agent_id:
            return {"created": False, "error": "name or id required"}
        try:
            spec = self.registry.create(
                agent_id,
                name=name,
                description=str(params.get("description") or "").strip(),
                identity=str(params.get("identity") or params.get("instructions") or "").strip(),
                # optional APP AGENT scaffold: "" = chat only, "browser"/"window" = ship a
                # ui/ + [app] declaring how openers present it (docs/PROTOCOL.md §9)
                app=str(params.get("app") or "").strip().lower(),
            )
        except ValueError as e:
            return {"created": False, "error": str(e)}
        # show it right away, then fill colour/tagline in the background
        await self._broadcast_agents_changed()
        asyncio.create_task(self._maybe_generate_presentations())
        log.info("agents.create %s (%s)", spec.id, name or spec.id)
        return {"created": True, "agentId": spec.id, "name": spec.name}

    async def _broadcast_agents_changed(self) -> None:
        """Tell every connected client the agent ROSTER changed, so it redraws its list."""
        await self._send_all(dump_frame(Event(event="agents.changed", payload=self._agents_list())))

    def broadcast_agents_changed(self) -> None:
        """SYNC, fire-and-forget wrapper handed to PLUGINS via PluginContext.

        A plugin that alters the roster (agent-builder's reload_agent) runs inside ordinary
        synchronous tool code, so it cannot await. The send is scheduled on the running loop
        instead — a notification nobody waits on. Outside a loop (unit tests) it is a no-op,
        which is why the plugin treats the handle as best-effort."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        asyncio.create_task(self._broadcast_agents_changed())

    def _workspace_cleanup(self, params: dict) -> dict:
        """Tidy an agent's workspace: delete scratch (all of <workspace>/tmp/) + any file
        matching the given glob patterns. Dry-run by DEFAULT (returns what WOULD be deleted);
        apply=true actually deletes. Stale index rows auto-prune on the agent's next turn."""
        from agent_runtime.infrastructure.workspace.cleanup import cleanup, plan_cleanup

        agent_id = (params.get("agentId") or "main").strip().lower()
        patterns = tuple(params.get("patterns") or ())
        apply = bool(params.get("apply"))
        if self.registry is None:
            return {"error": "no agent registry"}
        try:
            ws = self.registry.get(agent_id).workspace
        except KeyError:
            return {"error": f"no such agent: {agent_id}"}
        if apply:
            deleted = cleanup(ws, patterns=patterns)
            log.info("workspace.cleanup %s -> deleted %d", agent_id, len(deleted))
            return {"agentId": agent_id, "applied": True, "deleted": deleted, "count": len(deleted)}
        targets = plan_cleanup(ws, patterns=patterns)
        return {
            "agentId": agent_id,
            "applied": False,
            "wouldDelete": targets,
            "count": len(targets),
        }

    def _cron_list(self) -> dict:
        """Scheduled jobs across ALL agents + recent runs — the uniform 'list my jobs'
        surface any client can render (same source of truth as the cron tool)."""
        if self.task_store is None:
            return {"autonomy": False, "jobs": [], "runs": []}

        def sched(t) -> str:
            if t.kind == "cron":
                return f"cron '{t.cron_expr}'" + (f" {t.tz}" if t.tz else "")
            if t.kind == "every":
                return f"every {int(t.every_seconds)}s"
            return "once"

        jobs = [
            {
                "id": t.id,
                "agentId": t.agent_id,
                "kind": t.kind,
                "schedule": sched(t),
                "nextDue": datetime.fromtimestamp(t.next_due).strftime("%Y-%m-%d %H:%M"),
                "enabled": bool(t.enabled),
                "delivery": t.delivery,
                "payload": t.payload,
            }
            for t in self.task_store.list(None)
        ]
        runs = [
            {
                "taskId": r.task_id,
                "agentId": r.agent_id,
                "status": r.status,
                "detail": r.detail,
                "at": datetime.fromtimestamp(r.started_at).strftime("%Y-%m-%d %H:%M"),
            }
            for r in self.task_store.recent_runs(limit=10)
        ]
        return {"autonomy": True, "jobs": jobs, "runs": runs}

    def _cron_runs(self, params: dict) -> dict:
        """Full run history — the uniform 'view history' surface any client can render.
        Optional id (one job) / agentId filter; limit (default 200)."""
        if self.task_store is None:
            return {"autonomy": False, "runs": []}
        tid = (params.get("id") or "").strip() or None
        aid = (params.get("agentId") or "").strip() or None
        try:
            limit = max(1, min(int(params.get("limit", 200)), 1000))
        except (TypeError, ValueError):
            limit = 200
        runs = [
            {
                "id": r.id,
                "taskId": r.task_id,
                "agentId": r.agent_id,
                "status": r.status,
                "outcome": r.outcome,
                "detail": r.detail,
                "startedAt": datetime.fromtimestamp(r.started_at).strftime("%Y-%m-%d %H:%M:%S"),
                "finishedAt": (
                    datetime.fromtimestamp(r.finished_at).strftime("%H:%M:%S")
                    if r.finished_at
                    else None
                ),
                "durationSec": (round(r.finished_at - r.started_at, 1) if r.finished_at else None),
            }
            for r in self.task_store.recent_runs(agent_id=aid, task_id=tid, limit=limit)
        ]
        return {"autonomy": True, "runs": runs}

    def _require_store(self):
        if self.task_store is None:
            raise RuntimeError("autonomy is off — set AGENTD_AUTONOMY=1 and restart the gateway")
        return self.task_store

    def _cron_add(self, params: dict) -> dict:
        """Create a job for an agent (client-driven; mirrors the cron tool's 'add')."""
        from agent_runtime.infrastructure.autonomy.schedule import resolve_schedule

        store = self._require_store()
        payload = (params.get("payload") or "").strip()
        if not payload:
            raise ValueError("payload is required")
        agent_id = (params.get("agentId") or "main").strip() or "main"
        if self.registry is not None and agent_id not in self.registry.list_ids():
            raise ValueError(f"unknown agent: {agent_id}")
        deliver = (params.get("deliver") or "run").strip()
        tid = uuid.uuid4().hex[:12]
        task = ScheduledTask(
            id=tid,
            agent_id=agent_id,
            session_key=cron_session_key(agent_id, tid),
            payload=payload,
            enabled=True,
            created_at=time.time(),
            delivery=deliver if deliver in ("run", "message") else "run",
            **resolve_schedule(params),
        )
        store.add(task)
        return {"id": task.id}

    def _cron_update(self, params: dict) -> dict:
        from agent_runtime.infrastructure.autonomy.schedule import resolve_schedule

        store = self._require_store()
        tid = (params.get("id") or "").strip()
        if not tid or store.get(tid) is None:
            raise ValueError(f"no such job: {tid}")
        fields: dict = {}
        if any(params.get(k) for k in ("cron", "daily", "every", "in", "at")):
            fields.update(resolve_schedule(params))
            fields["enabled"] = 1
        if params.get("payload"):
            fields["payload"] = params["payload"].strip()
        if params.get("deliver") in ("run", "message"):
            fields["delivery"] = params["deliver"]
        if "enabled" in params:
            fields["enabled"] = 1 if params["enabled"] else 0
        if not fields:
            raise ValueError("nothing to update")
        store.update(tid, **fields)
        return {"ok": True, "id": tid}

    def _cron_remove(self, params: dict) -> dict:
        store = self._require_store()
        tid = (params.get("id") or "").strip()
        return {"removed": bool(tid and store.remove(tid)), "id": tid}

    def _cron_run(self, params: dict) -> dict:
        store = self._require_store()
        tid = (params.get("id") or "").strip()
        if not tid or store.get(tid) is None:
            raise ValueError(f"no such job: {tid}")
        store.update(tid, next_due=time.time(), enabled=1)  # fires on the next scheduler poll
        return {"ok": True, "id": tid}

    def _platform_status(self) -> dict:
        """What a client needs to know to sign in and to show its own mode.

        THE DAEMON ANSWERS TWO QUESTIONS AND OWNS NEITHER FACT. `accountsUrl` says where to log
        in — the client POSTs there itself and keeps the token. `mode` and `signedIn` describe
        THIS CONNECTION, because both arrive on it: a second window signed in as somebody else,
        or on its own API keys, gets different answers from the same daemon at the same moment.

        Nothing here is stored. An earlier version kept one session and one mode for the whole
        machine, which is fine for one person on a laptop and wrong for everyone else — the
        second person to sign in overwrote the first, and one window's Cloud switch moved every
        other window's billing."""
        proxy_status = model_proxy.status()
        account = accounts.current_account.get() or {}
        return {
            # Where to sign in. Resolved env > config > profile — one answer, so the client and
            # the daemon can never disagree about which service this build talks to.
            # The BROWSER-facing address, which on a hosted deployment is not the one this daemon
            # calls (that is internal service DNS a visitor cannot resolve).
            "accountsUrl": client_accounts_url(self.config),
            # WHO is on this connection, and WHICH KEYS its calls run on.
            "signedIn": bool(account.get("account_id")),
            "email": str(account.get("email") or ""),
            "accountId": str(account.get("account_id") or ""),
            "mode": model_proxy.CLOUD if model_proxy.enabled() else model_proxy.LOCAL,
            # Is there a Cloud to switch to at all on this build?
            "canUseCloud": model_proxy.available(),
            "modelProxy": proxy_status,
            # Wire compatibility for already-shipped clients. New clients read modelProxy.
            "modelGateway": proxy_status,
            # Diagnostics (5.1). `available` is what the settings UI gates the toggle on: a build
            # with no ingest endpoint must not offer to send anywhere, and "we couldn't send it"
            # is a worse answer than never having asked.
            "diagnostics": {
                "available": bool(telemetry.resolve_ingest_url(self.config)),
                **telemetry.upload_status(),
            },
        }

    # platform.connect / platform.disconnect USED TO WRITE A CREDENTIAL — the caller's session
    # token, persisted as AGENTD_MODEL_PROXY_KEY, one key for the whole daemon. That is why an
    # unsigned window spent whoever had last pressed Cloud, and why two people on one machine
    # could not choose differently.
    #
    # Billing is now a property of the CONNECTION: the client says `?mode=` when it connects and
    # pays with the session it already presented. Nothing to persist, so these two are kept only
    # as a compatibility shim for older desktop shells that still call them on every handshake.

    def _platform_connect(self, params: dict) -> dict:
        """Deprecated. Mode arrives on the connection; this just reports where things stand."""
        return self._platform_status()

    def _platform_disconnect(self) -> dict:
        """Deprecated. See _platform_connect."""
        return self._platform_status()

    def _platform_set_model_proxy_url(self, params: dict) -> dict:
        """Set (or clear) the desktop Cloud-mode Model Proxy URL override. Persists
        model_proxy.api_base to agentd.config.json, updates the live config, and re-runs
        model_proxy.configure() so it applies immediately (a currently-connected session
        retargets live). An empty url clears the override, falling back to the baked
        distribution default. No-op on a hosted (accounts-mode) daemon."""
        if accounts.enabled():
            raise ValueError("this deployment manages the model proxy server-side")
        url = str(params.get("url") or "").strip().rstrip("/")
        mp = dict(
            getattr(self.config, "model_proxy", None)
            or getattr(self.config, "model_gateway", None)
            or {}
        )
        if url:
            mp["api_base"] = url
        else:
            mp["api_base"] = ""
        try:
            self.config.model_proxy = mp  # live config for this process
        except Exception:  # noqa: BLE001 — persistence below is the durable path
            pass
        _persist_config_patch({"model_proxy": mp})
        model_proxy.configure(self.config)
        return self._platform_status()

    def _hello(self, params: dict | None = None) -> dict:
        """Handshake: identity + status a client renders as its welcome banner.

        The agent NAME (and all these facts) are owned by the server's config — the
        single source of truth — so every front-end shows the same thing without
        hardcoding any of it.

        A client MAY introduce itself: `{protocol: <int it speaks>, client: "name/ver"}`.
        The reply then carries `compatible` (advisory in v1 — the server advertises,
        it never rejects) so third-party clients built on the published protocol can
        detect a mismatch and degrade gracefully instead of breaking silently.
        """
        p = params or {}
        client_protocol = p.get("protocol")
        client_name = str(p.get("client") or "").strip()
        if client_name:
            log.info("hello from client %s (protocol %s)", client_name, client_protocol)
        distribution = getattr(self.config, "distribution", None)
        return {
            "agentName": self.config.agent_name,
            "agentId": self.config.agent_id,
            "model": _effective_model(self.config),
            "reasoning": self.config.reasoning_effort,
            "gatewayUrl": f"ws://{self.config.host}:{self.config.port}",
            "workspace": str(self.config.workspace),
            "sessions": len(list_sessions(self.config.state_dir)),
            # M2 versioning: clients adapt to the daemon, never the reverse.
            "version": __version__,
            "protocol": PROTOCOL_VERSION,
            "compatible": (
                not isinstance(client_protocol, int) or client_protocol <= PROTOCOL_VERSION
            ),
            # M6 flavor: what THIS INSTALL is (branding + whether the store shows).
            "product": getattr(distribution, "product_name", "agentd"),
            "productId": getattr(distribution, "product_id", "agentd"),
            "storeEnabled": bool(getattr(distribution, "store_enabled", True)),
            "registryConfigured": bool(getattr(self.config, "registry_url", "")),
            "registryUrl": str(getattr(self.config, "registry_url", "") or ""),
            # where a LOCAL registry is auto-detected — clients can show real setup
            # instructions instead of a bare error (local-first store).
            "localRegistryDir": str(Path(self.config.state_dir) / "registry"),
            "agents": self._agents_list()["agents"],  # so any client can show/pick agents
            # hosted platform: where sign-in lives + whether platform keys are active — so a
            # client can gate on sign-in / render the keys indicator from the handshake alone.
            "platform": self._platform_status(),
        }

    def _platform_keys_locked(self) -> bool:
        """Provider keys are PLATFORM-managed (not user-editable) whenever the daemon routes models
        through the Model Proxy — i.e. the desktop exe's Cloud mode and any hosted/AWS daemon. In
        local BYOK mode this is False and the user edits their own keys. Consumed by _config_get
        (render the API-Keys section read-only) and enforced by _config_set (refuse key writes)."""
        try:
            from agent_runtime.infrastructure.llm import model_proxy

            return bool(model_proxy.enabled())
        except Exception:
            return False

    def _is_installed_agent(self, agent_id: str) -> bool:
        """Did this agent arrive inside a downloaded .agentpkg, rather than ship with the
        product / get authored here? The installer's ledger decides — never the agent's own
        files — so a package cannot vouch for itself. Same rule the plugin sandbox uses.

        FAILS CLOSED: an unreadable ledger returns True, so a broken ledger redacts rather
        than leaks."""
        if not agent_id:
            return False  # a host connection, not an app
        from pathlib import Path

        from agent_runtime.infrastructure.marketplace.installed_store import JsonInstalledStore

        ids = JsonInstalledStore(
            Path(self.config.state_dir) / "installed_bundles.json"
        ).installed_ids()
        return True if ids is None else agent_id in ids

    def _declared_settings(self, scope: str | None) -> tuple:
        """What ``agent.toml [[settings]]`` says this agent needs — the fields, never the values.

        Empty for a HOST connection (``scope`` is None): the desktop settings page is the whole
        machine's, and there is no one agent whose declarations would apply.

        An unknown id RAISES. A scoped page whose agent has been removed is broken, and returning
        "declares nothing" would render it as an agent that simply needs no settings — the one
        reading under which the bug is invisible.
        """
        if not scope or self.registry is None:
            return ()
        try:
            spec = self.registry.get(scope)
        except KeyError:
            raise RuntimeError(f"unknown agent: {scope}") from None
        return tuple(getattr(spec, "settings", ()) or ())

    def _config_get(self, scope: str | None = None) -> dict:
        """The editable-config surface the settings UI renders: the current effective value
        of every EXPOSED knob, provider-key presence (`env`) + values (`envValues`, so the local
        UI can reveal a saved key), the config file path, and the raw file text (Advanced editor).

        ``scope`` is the agent id when this comes from an APP window. A window belonging to an
        INSTALLED agent gets the settings surface with every secret-bearing field removed — see
        _redact_for_installed_agent for what and why."""
        import json

        cfg = self.config
        declared = self._declared_settings(scope)
        values: dict = {}
        for key in EXPOSED_CONFIG_KEYS:
            if hasattr(cfg, key):
                values[key] = _json_safe(getattr(cfg, key))
        # MCP servers are managed via mcp.* but shown here read-only for context
        values["mcp_servers"] = [_server_dict(s) for s in (cfg.mcp_servers or [])]
        path = _config_file_path()
        try:
            raw = path.read_text(encoding="utf-8") if path.is_file() else ""
        except OSError:
            raw = ""
        # pretty-print the raw file so the Advanced editor is readable even if hand-minified
        if raw.strip():
            try:
                raw = json.dumps(json.loads(raw), indent=2) + "\n"
            except ValueError:
                pass
        payload = {
            "path": str(path),
            "exists": path.is_file(),
            "envPath": str(path.parent / ".env"),
            "values": values,
            # Presence, by the name the PAGE knows. A declared key is stored prefixed, so the
            # lookup goes through setting_env_name while the reply stays in the author's terms —
            # the page never learns the prefix exists.
            "env": {
                **{name: bool(os.environ.get(name)) for name in PROVIDER_ENV_KEYS},
                **{
                    f.key: bool(os.environ.get(setting_env_name(scope or "", f.key)))
                    for f in declared
                },
            },
            # actual values so the local UI can reveal a saved key (masked by default); loopback + token-authed
            "envValues": {name: os.environ.get(name, "") for name in PROVIDER_ENV_KEYS},
            "providerKeys": list(PROVIDER_ENV_KEYS),
            # What THIS agent's author declared it needs (agent.toml [[settings]]): the shape only.
            "settings": [
                {
                    "key": f.key,
                    "label": f.label,
                    "kind": f.kind,
                    "required": f.required,
                    "help": f.help,
                }
                for f in declared
            ],
            # Values for the NON-SECRET declared fields. A url or a text field the user typed has
            # to be readable or they cannot correct a typo without retyping the whole thing; a
            # secret never is, installed or not. `envValues` cannot serve this — it is stripped
            # wholesale for an installed agent, which is exactly where these fields matter most.
            "settingsValues": {
                f.key: os.environ.get(setting_env_name(scope or "", f.key), "")
                for f in declared
                if not f.secret
            },
            # {config_key: AGENTD_VAR} for knobs an env var currently PINS — the UI marks these
            # read-only so a save never silently reverts on the next daemon boot.
            "envOverrides": {
                k: v for k, v in EXPOSED_KEY_ENV.items() if os.environ.get(v) not in (None, "")
            },
            # option-sets a client renders as dropdowns (value + display label + group). Any
            # field can reference one of these by key — scalable: add a catalog, reference it.
            "catalogs": _kind_catalogs(cfg),
            "raw": raw,
            "effectiveModel": _effective_model(cfg),
            "version": __version__,
            # read-only hosted-platform state for the Settings indicator (platform keys vs BYOK)
            "platform": self._platform_status(),
            # cloud/platform mode: provider keys live on the Model Proxy, so the UI must render the
            # API-Keys section read-only. Edits are also refused server-side (see _config_set (c)).
            "keysLocked": self._platform_keys_locked(),
        }
        return self._redact_for_installed_agent(payload, scope)

    @staticmethod
    def _redact_secret_fields(payload: dict) -> dict:
        """Strip every field that hands out a secret or the shape of this machine.

        Not just `envValues`. An app window is a page that may have shipped inside someone
        else's package, and /apps/ pages have no CSP — so anything readable is exfiltratable:

          envValues  the provider keys themselves
          raw        the WHOLE config file text (the Advanced editor's payload — whatever the
                     user keeps in there, including keys they pasted by hand)
          path       absolute path of the config file
          envPath    absolute path of the .env

        What survives is what a settings page actually needs: `values` (the curated
        EXPOSED_CONFIG_KEYS), `env` presence booleans, catalogs, and the platform indicators.
        So BYOK still works — the page can say "no key set", take one, and save it through
        config.set. It just can never read one back.
        """
        return {k: v for k, v in payload.items() if k not in ("envValues", "raw", "path", "envPath")}

    def _redact_for_installed_agent(self, payload: dict, scope: str | None) -> dict:
        """Host connections and locally-authored agents get everything; a DOWNLOADED agent's
        window gets the redacted surface."""
        if scope and self._is_installed_agent(scope):
            return self._redact_secret_fields(payload)
        return payload

    # What an INSTALLED agent's page may put in `config.set`'s `patch`. The `keys` half has had an
    # allowlist since [[settings]] landed; this is the other half, and it was wide open —
    # WRITABLE_CONFIG_KEYS is every exposed knob, so a settings page that shipped inside somebody
    # else's download could repoint `state_dir`, turn off `sandbox_untrusted_plugins`, or switch on
    # `mcp_workshop` and then chat-drive itself into running arbitrary commands.
    #
    # INSTALLED, not merely scoped. The line is the one this file already draws for the
    # secret-bearing fields of `config.get` (see _redact_for_installed_agent): code that arrived
    # in a package is not code the user wrote. A locally-authored agent's page keeps the whole
    # surface, which is what lets Agent Builder's own settings window edit the daemon — it is the
    # user's own tool, on the user's own machine.
    #
    # These are what a settings page legitimately offers for the daemon-wide groups: `agents`
    # arrives as one nested top-level key and is PRUNED to the connection's own id below, so an
    # agent can only ever edit its own block. The rest are preferences — which model, how hard it
    # thinks, what the assistant is called — not machine plumbing.
    SCOPED_PATCH_KEYS = frozenset({"agents", "model", "reasoning_effort", "cost_efficiency", "agent_name"})

    def _scoped_patch(self, patch: dict, scope: str) -> tuple[dict, list[str]]:
        """Narrow an app connection's patch to what it may write. Returns (kept, refused)."""
        kept: dict = {}
        refused: list[str] = []
        for key, value in patch.items():
            if key not in self.SCOPED_PATCH_KEYS:
                refused.append(key)
                continue
            if key == "agents":
                own = (value or {}).get(scope) if isinstance(value, dict) else None
                for other in (value or {}) if isinstance(value, dict) else ():
                    if other != scope:
                        refused.append(f"agents.{other}")
                if own is not None:
                    kept["agents"] = {scope: own}
                continue
            kept[key] = value
        return kept, refused

    def _writable_env_names(self, scope: str | None) -> frozenset[str]:
        """Which .env names ``config.set`` will write for this connection.

        The provider keys, plus — on a scoped app connection — exactly the names that agent's
        own ``agent.toml`` declares under ``[[settings]]``. A DECLARATION IS A PERMISSION: an
        author says "my agent needs COINBASE_API_KEY", and that sentence is what makes the field
        writable from their page. Nothing they did not declare is.

        Without this, ``keys`` wrote any name at all: a settings page that shipped inside a
        downloaded package could overwrite ANTHROPIC_API_KEY with a key of its own choosing and
        bill the user's turns to somebody else, and the page is trusted enough to be shown.

        A HOST connection (the desktop UI, the terminal client) gets every agent's declarations,
        not none: it is the machine's owner, and "this agent" names nobody there. It is still an
        allowlist — a host page cannot invent an env name either.
        """
        allowed = set(PROVIDER_ENV_KEYS)
        if scope:
            allowed |= {f.key for f in self._declared_settings(scope)}
        elif self.registry is not None:
            for agent_id in self.registry.list_ids():
                allowed |= {f.key for f in self._declared_settings(agent_id)}
        return frozenset(allowed)

    # ------------------------------------------------------------------ OAuth (Part 2b)

    def _oauth_redirect_uri(self) -> str:
        """Where the provider sends the user back.

        The daemon's OWN port, at a fixed path — not a random loopback port. Most providers
        require the redirect URI to be registered up front, and one that changes every run cannot
        be registered at all.
        """
        return f"http://127.0.0.1:{getattr(self.config, 'port', 8765)}/oauth/callback"

    def _oauth_decl(self, agent_id: str, name: str):
        spec = self.registry.get(agent_id) if (self.registry and agent_id) else None
        decl = next((d for d in getattr(spec, "oauth", ()) or () if d.name == name), None)
        if decl is None:
            raise ValueError(f"agent '{agent_id}' declares no OAuth connection '{name}'")
        return decl

    async def _oauth_connect(self, params: dict) -> dict:
        """Start a sign-in and hand back the URL. THE CLIENT OPENS IT, not the daemon.

        Right on a desktop, where they are the same machine, and the only thing that works when
        the UI is a tab somewhere else: a daemon calling `webbrowser.open` there would launch a
        browser nobody is sitting in front of.
        """
        if self.oauth_service is None:
            return {"started": False, "error": "this daemon has no OAuth support"}
        agent_id = str(params.get("agentId") or "")
        name = str(params.get("name") or "")
        url = await self.oauth_service.begin(
            agent_id, self._oauth_decl(agent_id, name), self._oauth_redirect_uri()
        )
        return {"started": True, "authorizeUrl": url, "agentId": agent_id, "name": name}

    def _oauth_status(self, params: dict) -> dict:
        if self.oauth_service is None:
            return {"connections": []}
        agent_id = str(params.get("agentId") or "")
        spec = self.registry.get(agent_id) if (self.registry and agent_id) else None
        return {
            "agentId": agent_id,
            "connections": self.oauth_service.status(agent_id, getattr(spec, "oauth", ()) or ()),
        }

    def _oauth_disconnect(self, params: dict) -> dict:
        if self.oauth_service is None:
            return {"disconnected": False}
        agent_id = str(params.get("agentId") or "")
        name = str(params.get("name") or "")
        self._oauth_decl(agent_id, name)  # refuse a name this agent never declared
        ok = self.oauth_service.disconnect(agent_id, name)
        # A connection an MCP server was riding on just went away; drop the session so the next
        # run reports "not connected" instead of using a token that no longer exists.
        if self.mcp_connector is not None:
            self.mcp_connector.forget(agent_id)
        return {"disconnected": ok, "agentId": agent_id, "name": name}

    async def _serve_oauth_callback(self, split) -> HttpResponse:
        """Where the provider sends the user's browser back to.

        Returns a PAGE, not JSON: a human is looking at this, and "close this tab" is the whole
        remaining instruction.

        The exchange is AWAITED, so the page states what actually happened. It was originally
        scheduled as a task because this handshake hook used to be synchronous; now that it is a
        coroutine, awaiting costs one round trip to the token endpoint and buys the difference
        between "Signed in" and "Signed in (probably — check the logs)". A sign-in that says it
        worked and did not is the single worst outcome here: the user goes back to the agent,
        finds it still not connected, and has nothing to act on.
        """
        query = parse_qs(split.query or "")
        state = (query.get("state") or [""])[0]
        code = (query.get("code") or [""])[0]
        error = (query.get("error_description") or query.get("error") or [""])[0]
        if error:
            return _oauth_page(f"Sign-in failed: {error}")
        if not (state and code):
            return _oauth_page("Sign-in failed: the provider sent no authorization code.")
        if self.oauth_service is None:
            return _oauth_page("Sign-in failed: this daemon has no OAuth support.")

        try:
            await self.oauth_service.complete(state, code)
        except Exception as e:  # noqa: BLE001 — the browser is the only place this can be reported
            log.warning("oauth: callback failed: %s", e)
            return _oauth_page(f"Sign-in failed: {e}")
        return _oauth_page("Signed in. You can close this tab and go back to the agent.")

    def _redial_mcp(self, changed: set[str]) -> None:
        """Make every agent whose declared MCP server references one of ``changed`` reconnect."""
        if self.mcp_connector is None or self.registry is None:
            return
        agents = []
        for agent_id in self.registry.list_ids():
            try:
                agents.append(self.registry.get(agent_id))
            except KeyError:
                continue
        for agent_id in self.mcp_connector.agents_using(agents, changed):
            self.mcp_connector.forget(agent_id)
            log.info("mcp: '%s' will reconnect — a setting it uses changed", agent_id)

    def _mcp_status(self, params: dict) -> dict:
        """What this agent DECLARED, and where each one stands.

        The answer to "why does this agent have no tools" — a question that otherwise has no
        answer anywhere: the model just says it cannot do the thing.
        """
        agent_id = str(params.get("agentId") or "")
        declared = self._declared_settings  # reuse the same unknown-agent guard
        declared(agent_id or None)
        spec = self.registry.get(agent_id) if (self.registry and agent_id) else None
        problems = (
            self.mcp_connector.problems_for(agent_id) if self.mcp_connector is not None else {}
        )
        live = self.mcp_connector.tools_for(agent_id) if self.mcp_connector is not None else []
        servers = []
        for decl in getattr(spec, "mcp", ()) or ():
            servers.append(
                {
                    "name": decl.name,
                    "transport": decl.transport,
                    # The exact argv, because approving "the aws server" is not consent to
                    # whatever that name means in the next version.
                    "command": list(decl.command),
                    "url": decl.url,
                    "needs": list(decl.placeholders),
                    "problem": problems.get(decl.name, ""),
                    "tools": [
                        t.name for t in live if getattr(t, "name", "").startswith(f"{decl.name}__")
                    ],
                }
            )
        return {"agentId": agent_id, "servers": servers}

    def _mcp_approve(self, params: dict) -> dict:
        """Record the user's consent to launch one declared server's exact command."""
        if self.mcp_connector is None:
            return {"approved": False, "error": "this daemon has no declared-MCP support"}
        agent_id = str(params.get("agentId") or "")
        name = str(params.get("name") or "")
        spec = self.registry.get(agent_id) if (self.registry and agent_id) else None
        decl = next((d for d in getattr(spec, "mcp", ()) or () if d.name == name), None)
        if decl is None:
            raise ValueError(f"agent '{agent_id}' declares no MCP server '{name}'")
        ok = self.mcp_connector.approve(agent_id, decl)
        return {"approved": ok, "agentId": agent_id, "name": name}

    def _env_write_plan(self, keys: dict, target: str) -> tuple[dict, list[str], list[str]]:
        """Turn ``{name: value}`` as the PAGE wrote it into ``{env_name: value}`` for the .env.

        Returns (writes, refused, ambiguous):

          writes     provider keys under their own name; ``target``'s declared keys under their
                     private prefixed name
          refused    everything else — a name nobody declared is not writable from anywhere
          ambiguous  a name SOME agent declares, sent without saying which agent. Two agents can
                     each declare ``AWS_ACCESS_KEY_ID`` for two different accounts, so guessing
                     would write one account's key over the other's. The caller says which.

        Note that ONE declaring agent is still ambiguous. Picking it would be right today and
        silently wrong the day a second agent declares the same name — and the failure then is a
        credential written into the wrong agent's slot, which nothing downstream can detect.
        """
        declared = {f.key for f in self._declared_settings(target or None)}
        everyones: set[str] = set()
        if not target and self.registry is not None:
            for agent_id in self.registry.list_ids():
                everyones |= {f.key for f in self._declared_settings(agent_id)}

        writes: dict = {}
        refused: list[str] = []
        ambiguous: list[str] = []
        for name, value in keys.items():
            if name in PROVIDER_ENV_KEYS:
                writes[name] = str(value)
            elif name in declared:
                writes[setting_env_name(target, name)] = str(value)
            elif name in everyones:
                ambiguous.append(name)
            else:
                refused.append(name)
        return writes, sorted(refused), sorted(ambiguous)

    def _config_set(self, params: dict, scope: str | None = None) -> dict:
        """Persist config edits. Three independent, composable inputs:
          * ``patch``  {key: value} for EXPOSED_CONFIG_KEYS -> merged into the JSON file
            (all other keys preserved) AND hot-applied to the in-memory Config.
          * ``keys``   {ENV_NAME: value} provider secrets + this agent's DECLARED settings ->
            written to the .env sibling of the config file and applied live (empty value
            removes the key). Any other name is refused — see _writable_env_names.
          * ``raw``    a full JSON document -> overwrites the whole config file (Advanced editor).
        A daemon restart guarantees full effect (some knobs only bind at startup)."""
        import json

        patch = params.get("patch") or {}
        keys = params.get("keys") or {}
        raw = params.get("raw")
        # WHOSE settings these are. The dispatcher already forces `agentId` to the connection's own
        # scope on every write, so an app can only ever target itself; a HOST connection names the
        # agent explicitly (and must, when the name is one that several agents declare).
        target = str(params.get("agentId") or scope or "")

        # (a) raw full-file overwrite — the Advanced editor, and the widest door in this method:
        # it replaces the WHOLE config file, which would walk straight past both allowlists below.
        # Host connections only. An app window asking to rewrite the machine's config is not a
        # settings page, whatever it looks like.
        if self._is_installed_agent(scope) and isinstance(raw, str) and raw.strip():
            return {
                "saved": False,
                "error": "a downloaded agent's page cannot replace the daemon config file",
            }
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
            except ValueError as e:
                return {"saved": False, "error": f"invalid JSON: {e}"}
            if not isinstance(parsed, dict):
                return {"saved": False, "error": "config must be a JSON object"}
            path = _config_file_path()
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(parsed, indent=2) + "\n", encoding="utf-8")
            except OSError as e:
                return {"saved": False, "error": str(e)}
            return {"saved": True, "path": str(path), "restartRecommended": True}

        result: dict = {"saved": False, "restartRecommended": False}

        # (b) whitelisted key/value patch. A DOWNLOADED agent's page is narrowed first — to its
        # own agents.<id> block and a handful of preferences — and told what was dropped, because
        # a page whose save silently did nothing is worse than one that says it may not.
        if scope and self._is_installed_agent(scope):
            patch, patch_refused = self._scoped_patch(patch, scope)
            if patch_refused:
                return {
                    "saved": False,
                    "error": (
                        f"not writable from this agent's page: {', '.join(patch_refused)}. An "
                        f"agent may edit its own settings and a few shared preferences — not the "
                        f"daemon's plumbing."
                    ),
                    "refused": patch_refused,
                }
        clean = {k: v for k, v in patch.items() if k in WRITABLE_CONFIG_KEYS}
        if clean:
            ok, path = _persist_config_patch(clean)
            if not ok:
                return {"saved": False, "error": f"could not write {path}"}
            for k, v in clean.items():  # hot-apply (full effect on restart)
                try:
                    if k in PATH_CONFIG_KEYS and isinstance(v, str):
                        setattr(self.config, k, Path(v).expanduser())
                    else:
                        setattr(self.config, k, v)
                except Exception:  # noqa: BLE001 — a bad value never crashes the save
                    pass
            if "diagnostics_upload" in clean:
                # CONSENT MUST TAKE EFFECT NOW, not on the next restart. Everything else here is
                # allowed to be "restart recommended"; a switch that says diagnostics are off
                # while they are still being sent is not a stale setting, it is a false statement.
                telemetry.apply_diagnostics(self.config)
            result.update(saved=True, path=path, restartRecommended=True)

        # (c) provider keys + this agent's declared settings -> the .env next to the config file.
        if keys:
            # Cloud/platform mode locks the PROVIDER keys only: those are the proxy's, so editing
            # them here is meaningless (the daemon routes through the proxy) and misleading. The
            # UI greys exactly those fields out via keysLocked.
            #
            # AN AGENT'S OWN [[settings]] ARE NOT PROVIDER KEYS. This gate used to sit in front of
            # the whole payload, so signing in and choosing Cloud silently refused a user's AWS
            # credential on their own machine — the save reported nothing, the field stayed "not
            # set", and no part of the UI could explain it. Which model pays for a turn has no
            # bearing on whether someone may store a third-party credential locally.
            locked = (
                sorted(k for k in keys if k in PROVIDER_ENV_KEYS)
                if self._platform_keys_locked()
                else []
            )
            if locked:
                return {
                    "saved": False,
                    "error": (
                        f"provider keys are managed by the platform in cloud mode and cannot be "
                        f"changed here: {', '.join(locked)}"
                    ),
                    "refused": locked,
                }
            writes, refused, ambiguous = self._env_write_plan(keys, target)
            if refused:
                return {
                    "saved": False,
                    "error": (
                        f"not writable from here: {', '.join(refused)}. A settings page may write "
                        f"the provider keys and the names its own agent.toml declares under "
                        f"[[settings]] — nothing else."
                    ),
                    "refused": refused,
                }
            if ambiguous:
                return {
                    "saved": False,
                    "error": (
                        f"name the agent for: {', '.join(ambiguous)}. These belong to an agent "
                        f"rather than to the machine — pass agentId to say whose value this is."
                    ),
                    "ambiguous": ambiguous,
                }
            env_file = _user_env_file()
            wrote = env_file.update(writes)
            result["envPath"] = str(env_file.path)
            result["keysApplied"] = wrote
            if wrote:
                # A running MCP child holds the environment it was LAUNCHED with, so a new key
                # changes nothing until the process is replaced. Forget the affected agents and
                # the next run re-dials with what was just saved.
                self._redial_mcp(set(keys))
            result["saved"] = result["saved"] or wrote

        if not clean and not keys:
            result["saved"] = True  # nothing to change is not a failure
        return result

    async def _chat_send(
        self, params: dict, client_id: str | None = None, account: dict | None = None
    ) -> dict:
        session_key = params.get("sessionKey") or "default"
        message = params.get("message") or ""

        # explicit agent selection (any client names the agent; the registry resolves
        # it — no client knows the session-key format). Unknown id -> clear error.
        agent_id = params.get("agentId") or None
        if agent_id and self.registry is not None and agent_id not in self.registry.list_ids():
            raise ValueError(f"unknown agent: {agent_id}")

        # user attachments (e.g. an edited image sent from the canvas): save each into the
        # target agent's workspace/uploads and carry them BY REFERENCE (Artifact). A message
        # may be attachments-only (no text), so the empty-check comes AFTER resolving them.
        attachments = self._save_uploads(
            agent_id, params.get("attachments") or [], (params.get("projectId") or "").strip()
        )
        if not message.strip() and not attachments:
            raise ValueError("message must not be empty")

        # HOSTED metering gate: an account with a budget that has already reached this month's cap
        # cannot start a new turn. A fresh check (not the cached connect-time view) so spend that
        # accrued this session counts. No budget / accounts off => never blocks.
        # AN EXPIRED CREDENTIAL CANNOT START NEW WORK. The socket stays open and its identity
        # stands — the client is who it said it was — but the token it would pay with is dead, so
        # letting the turn begin just moves the failure to the first model call, where it surfaces
        # as an opaque provider error halfway through a run.
        #
        # A GRACE WINDOW past `exp` (AGENTD_AUTH_GRACE_S, default 120s) absorbs the ordinary race:
        # the client refreshes shortly before expiry, and a turn starting in that gap should not
        # be punished for a few seconds of clock skew. Runs ALREADY in flight are never touched by
        # this check — killing a long agent run mid-tool-call over a refresh timer would be worse
        # than the overrun it prevents.
        #
        # The typed code is what makes this recoverable: the client refreshes, calls auth.update,
        # and retries, with nothing to explain to the user.
        expires_at = float((account or {}).get("expires_at") or 0)
        if expires_at:
            try:
                grace = float(os.environ.get("AGENTD_AUTH_GRACE_S", "") or 120)
            except ValueError:
                grace = 120.0
            if time.time() > expires_at + grace:
                raise RuntimeError(
                    "auth_expired: your session token expired — refresh and retry"
                )

        # A LOCALLY-VERIFIED account carries no budget: that number lives in the ledger and moves
        # as money is spent, so a copy inside a ten-minute-old token would be stale by
        # construction (see accounts.resolve). We cannot tell from the token whether a cap exists,
        # so we ask — once per TURN, not per model call, which is the same cost this check always
        # had for capped accounts and is why it is not worth avoiding.
        needs_budget_check = account is not None and (
            account.get("budget_usd") is not None or account.get("verified") == "local"
        )
        if needs_budget_check:
            view = await accounts.check_budget(account.get("account_id"))
            if view and view.get("over"):
                raise RuntimeError(
                    f"monthly budget reached — this account has spent "
                    f"${view.get('spent_usd')} of its ${view.get('budget_usd')} limit. "
                    f"Usage resets next month."
                )

        # project membership: a chat started "inside a project" carries projectId; the
        # link lives on the session's meta sidecar (server data — every client sees it).
        # Cheap guard: only write when it actually changes.
        project_id = (params.get("projectId") or "").strip()
        if project_id:
            from agent_runtime.infrastructure.memory.local_store import (
                read_session_meta,
                write_session_meta,
            )

            _, state_dir = self._resolve_state_dir(agent_id)
            if read_session_meta(state_dir, session_key).get("projectId") != project_id:
                write_session_meta(state_dir, session_key, projectId=project_id)

        idem = params.get("idempotencyKey")
        if idem and idem in self.idempotency:
            return {"runId": self.idempotency[idem], "deduplicated": True}

        existing = self.runs.get(session_key)
        if existing is not None and existing.task is not None and not existing.task.done():
            # Refused BEFORE a run_id would have existed — exactly the class of failure that
            # used to be invisible. The client's traceId (below) is what makes it findable.
            telemetry.count(
                "run_refused_total", reason="active_run",
                _props={"trace_id": str(params.get("traceId") or "")[:64]},
            )
            raise RuntimeError(f"session '{session_key}' already has an active run")

        # THE TRACKING NUMBER. Prefer the id the CLIENT minted: it exists before this handler
        # runs, so a failure in validation/attachments/the guard above is still traceable, and
        # the client can quote it in a support ticket. Full uuid4 hex, not [:12] — this is now
        # a join key on the usage ledger queried across months, where 48 bits is not enough.
        run_id = str(params.get("traceId") or "").strip()[:64] or uuid.uuid4().hex
        if idem:
            self.idempotency[idem] = run_id
        handle = RunHandle(
            run_id=run_id, session_key=session_key, abort=asyncio.Event(), client_id=client_id
        )
        handle.task = asyncio.create_task(
            self._run(handle, message, agent_id=agent_id, attachments=attachments, account=account)
        )
        self.runs[session_key] = handle
        return {"runId": run_id, "attachments": [artifact_to_dict(a) for a in attachments]}

    def _save_uploads(
        self, agent_id: str | None, raw: list, project_id: str = ""
    ) -> list[Artifact]:
        """Persist client-supplied attachments into ``<workspace>/uploads`` and return them as
        domain Artifacts (by reference). A chat inside a PROJECT saves into the project's SHARED
        workspace — the SAME folder the run's file/exec tools bind to (§11) — so an upload and any
        tool output that lands next to it stay together; a standalone chat uses the agent's own
        workspace (unchanged). Each item is ``{name, mimeType?, dataBase64}``; oversized/malformed
        items are skipped rather than failing the whole send. IO lives HERE (presentation)."""
        if not raw:
            return []
        workspace = self._upload_workspace(agent_id, project_id)
        dest = Path(workspace) / "uploads"
        out: list[Artifact] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            b64 = item.get("dataBase64") or item.get("data") or ""
            name = item.get("name") or "attachment"
            # cheap size guard on the base64 string (decoded size ~= 3/4 of it)
            if not b64 or (len(b64) * 3) // 4 > UPLOAD_MAX_BYTES:
                log.warning("skipping upload %r: empty or over %d bytes", name, UPLOAD_MAX_BYTES)
                continue
            try:
                info = save_upload(dest, name, b64)
            except ValueError as e:
                log.warning("skipping upload %r: %s", name, e)
                continue
            out.append(Artifact(**info))
        return out

    def _resolve_workspace(self, agent_id: str | None) -> str:
        """The workspace dir uploads land in — the named agent's own, else the default.
        Mirrors _resolve_state_dir's resolution so files sit where that agent's tools look."""
        aid = (agent_id or "").strip()
        acct = accounts.account_id()
        if acct:  # HOSTED: uploads land in this account's own per-agent workspace
            return str(user_state.account_workspace(self.config.state_dir, acct, aid or "main"))
        if aid and self.registry is not None:
            try:
                return str(self.registry.get(aid).workspace)
            except KeyError:
                pass
        return str(self.config.workspace)

    def _upload_workspace(self, agent_id: str | None, project_id: str = "") -> str:
        """Where an upload lands: a PROJECT chat -> the project's SHARED workspace (§11 — the same
        folder the run's tools use), else the target agent's own workspace. Mirrors the run's
        _effective_workspace so uploads and tool outputs never diverge. A stale/unknown project
        falls back to the agent workspace."""
        pid = (project_id or "").strip()
        if pid:
            try:
                from agent_runtime.infrastructure.memory import projects_store

                if projects_store.get_project(self._projects_root(), pid) is not None:
                    return str(projects_store.project_workspace_dir(self._projects_root(), pid))
            except Exception:  # noqa: BLE001 — resolution is an enhancement, never blocks a send
                pass
        return self._resolve_workspace(agent_id)

    def _abort_handle(self, handle: RunHandle) -> bool:
        """Signal a run to stop: set its abort flag (cooperative — the loop/tools
        check it) and cancel its task. Returns False if it wasn't running."""
        if handle.task is None or handle.task.done():
            return False
        handle.abort.set()
        handle.task.cancel()
        return True

    async def _abort_client_runs(self, client_id: str) -> None:
        """When a client connection ends, stop every in-flight run it started.

        Transport-agnostic: any front-end (terminal, desktop, mobile, a messaging
        channel adapter) that drops its connection has its own work cancelled —
        e.g. a computer-use run stops driving the PC the moment you close the app.
        Runs started by OTHER clients are untouched.
        """
        for handle in list(self.runs.values()):
            if handle.client_id == client_id and self._abort_handle(handle):
                log.info(
                    "client %s disconnected; aborting run %s (session %s)",
                    client_id,
                    handle.run_id,
                    handle.session_key,
                )

    async def _chat_abort(self, params: dict) -> dict:
        session_key = params.get("sessionKey") or "default"
        handle = self.runs.get(session_key)
        if handle is None or not self._abort_handle(handle):
            return {"aborted": False, "reason": "no active run"}
        return {"aborted": True, "runId": handle.run_id}

    # -------------------------------------------------------------------- run

    async def _run(
        self,
        handle: RunHandle,
        message: str,
        mode: str = RunMode.INTERACTIVE,
        agent_id: str | None = None,
        attachments: list[Artifact] | None = None,
        account: dict | None = None,
    ) -> None:
        # The gateway (presentation) now only adapts transport: it provides the event
        # sink (broadcast) and delegates the actual work to the AgentService use-case.
        # `mode` distinguishes a normal client turn from an autonomous heartbeat tick;
        # `agent_id` is an explicit client agent selection (else resolved from the key).
        # HOSTED: pin the connection's account on the context for the whole turn so model
        # calls (model_proxy) and spend reporting downstream see WHO this run bills to.
        # No-op when accounts are off (account is None). create_task snapshotted this
        # context, so the pin is isolated to this run's task.
        _acct_token = accounts.set_account(account)
        # Start a per-turn spend accumulator ONLY for account-scoped turns (so desktop/local pays
        # zero overhead — add_usage becomes a single contextvar read). Reported once when done.
        _usage_token = accounts.start_usage() if account is not None else None
        # Bind the tracking number for the WHOLE run. create_task snapshotted this context, so
        # the binding is isolated to this run's task and every log line, metric and outbound
        # proxy header underneath picks it up with no argument threading.
        telemetry.bind(
            run_id=handle.run_id,
            parent_run_id=handle.parent_run_id,
            trigger=handle.trigger,
            agent_id=agent_id,
            account_id=(account or {}).get("account_id"),
        )
        # Same ids, but on an application-layer contextvar, so AgentService can stamp them onto
        # the RunContext without importing infrastructure (v2/.importlinter forbids it).
        set_trace_ids(handle.run_id, "")
        _run_started = time.perf_counter()
        async def on_event(event: AgentEvent) -> None:
            # RENDER seam: tag tool-result / assistant events with the media files they
            # produced (server-side detection = single source of truth for every client).
            self._enrich_artifacts(event)
            await self._broadcast(handle.session_key, handle.run_id, event, agent_id)
            # OBSERVABILITY: durably record EVERY event so a run is viewable even with no client
            # attached (cron/channel/heartbeat/sub-agent). Best-effort; never breaks the run.
            if self.event_log is not None:
                self.event_log.emit(handle.session_key, handle.run_id, event)
            # SUB-AGENT visibility: relay a compact beat to the PARENT's view so a blocked
            # parent shows its children working instead of going silent.
            if handle.parent_session_key:
                relayed = subagent_relay(handle.session_key, event)
                if relayed is not None:
                    await self._broadcast(handle.parent_session_key, handle.run_id, relayed)

        status = "ok"
        err_msg = ""
        try:
            await self.service.handle_message(
                handle.session_key,
                message,
                on_event,
                handle.abort,
                mode=mode,
                agent_id=agent_id,
                attachments=attachments,
            )
        except asyncio.CancelledError:
            status = "aborted"  # abort already broadcast agent_end(aborted) from the loop
        except Exception as e:
            status = "error"
            err_msg = str(e)
            log.exception("run %s crashed", handle.run_id)
            crash = AgentEvent("agent_end", {"stopReason": "error", "error": str(e)})
            await self._broadcast(handle.session_key, handle.run_id, crash, agent_id)
            if self.event_log is not None:
                self.event_log.emit(handle.session_key, handle.run_id, crash)
        finally:
            # The run-level numbers, emitted before anything else in the teardown can fail.
            # duration is what a user waited; outcome is whether they got an answer at all.
            telemetry.timing(
                "run_duration_ms",
                (time.perf_counter() - _run_started) * 1000,
                outcome=status,
                trigger=handle.trigger,
            )
            telemetry.count("run_total", outcome=status, trigger=handle.trigger)
            # Auto-title an interactive chat after its first exchange (LM-Studio style):
            # fire-and-forget so it never delays the run; skips cron/heartbeat/aborted and
            # sessions that already have a title. Titles are conversation data (server-side),
            # so every client shows the same name.
            if mode == RunMode.INTERACTIVE and handle.cron_run_id is None and status != "aborted":
                asyncio.create_task(self._maybe_generate_title(handle.session_key, agent_id))
            # RUN seam: fold the agent's declared outcome into the headline status via the
            # pure policy. With enforce_outcome on, a cron run that finished `ok` but
            # declared nothing becomes `incomplete` (no silent success) — a decoupled
            # layer you can cut with AGENTD_ENFORCE_OUTCOME=0.
            declared = take_run_outcome()  # (raw_status, detail) | None
            status, outcome, detail = resolve_run_outcome(
                status,
                declared,
                enforce=getattr(self.config, "enforce_outcome", True),
                is_cron=handle.cron_run_id is not None,
            )
            if handle.cron_run_id is not None and self.task_store is not None:
                try:
                    self.task_store.finish_run(
                        handle.cron_run_id, status, outcome=outcome, detail=detail or ""
                    )
                except Exception:  # noqa: BLE001
                    pass
            # reach the user when a SCHEDULED run couldn't finish on its own (5a) —
            # gated on cron_run_id so interactive/heartbeat runs never push a notice.
            if (
                self.notifier is not None
                and handle.cron_run_id is not None
                and status in ("blocked", "failed", "error", "incomplete")
            ):
                await self._notify_run(handle, status, detail or err_msg)
            # failure-alert escalation (S14): after N consecutive failed/incomplete runs,
            # AUTO-PAUSE the job so a broken task stops running (+ spamming) forever. The
            # job's own failure_alert wins; else the global default (cron_failure_alert_default).
            alert = handle.cron_failure_alert or getattr(
                self.config, "cron_failure_alert_default", 0
            )
            if (
                alert
                and self.task_store is not None
                and status in ("failed", "error", "aborted", "incomplete")
                and self.task_store.consecutive_failures(handle.cron_task_id) >= alert
            ):
                try:
                    self.task_store.update(handle.cron_task_id, enabled=0)
                    if self.notifier is not None:
                        await self._notify_run(
                            handle,
                            "failed",
                            f"paused after {alert} consecutive failed/incomplete runs — "
                            f"needs your attention.",
                        )
                except Exception:  # noqa: BLE001
                    pass
            # HOSTED metering: report this turn's total model spend to the account's ledger, once.
            if account is not None:
                u = accounts.read_usage()
                if u and (u.get("cost_usd") or u.get("in_tokens") or u.get("out_tokens")):
                    try:
                        report = await accounts.report_usage(
                            account.get("account_id"),
                            u.get("model") or "",
                            u.get("in_tokens", 0),
                            u.get("out_tokens", 0),
                            u.get("cost_usd", 0.0),
                        )
                        if report is not None:
                            log.info(
                                "metered account=%s +$%.6f (%d/%d tok) -> spent $%.6f%s",
                                account.get("account_id"),
                                u.get("cost_usd", 0.0),
                                u.get("in_tokens", 0),
                                u.get("out_tokens", 0),
                                report.get("spent_usd", 0.0),
                                " [OVER BUDGET]" if report.get("over") else "",
                            )
                    except Exception:  # noqa: BLE001 — metering must never break a run
                        log.exception("usage report failed")
            # unpin the account + accumulator (best-effort; the task's context is discarded anyway)
            accounts.reset_usage(_usage_token)
            accounts.reset_account(_acct_token)

    def _agent_for_key(self, session_key: str, agent_id: str | None = None) -> str:
        """The agent a session belongs to: an explicit id wins; else the internal
        `agent:<id>:...` key prefix; else the default agent. Lets every broadcast carry
        `agentId` so clients filter events WITHOUT knowing the session-key format
        (the format stays server-internal)."""
        if agent_id:
            return agent_id
        if session_key.startswith("agent:"):
            parts = session_key.split(":", 2)
            if len(parts) >= 2 and parts[1]:
                return parts[1]
        return getattr(self.config, "agent_id", "main")

    async def _broadcast(
        self, session_key: str, run_id: str, event: AgentEvent, agent_id: str | None = None
    ) -> None:
        # `ts` (epoch seconds) stamps every live event server-side, so all clients
        # show the same send time — and it matches the transcript's stored timestamps.
        # `agentId` tags which agent the run belongs to (protocol v1 additive field),
        # so any client — and a scoped app connection — can filter by agent.
        await self._send_all(
            dump_frame(
                Event(
                    event="chat.event",
                    payload={
                        "sessionKey": session_key,
                        "runId": run_id,
                        "agentId": self._agent_for_key(session_key, agent_id),
                        "ts": time.time(),
                        "event": event.to_dict(),
                    },
                )
            )
        )

    def _hosted(self) -> bool:
        return bool(getattr(self.config, "hosted", False))

    async def _send_all(self, frame: str) -> None:
        """Send one frame to every connected client that MAY OBSERVE it, pruning dead
        connections.

        TENANT ISOLATION, send side, and this is its one choke point: every live event —
        chat.event, sessions/projects/agents.changed, notification, marketplace.progress —
        passes through here, so agents, tools and future emitters inherit the rule with no
        declaration. The frame's OWNER is whoever is acting in the CURRENT context (every
        emission site already runs in its actor's context: RPC handlers in the connection's,
        runs in the run task's, cron in the task's — the same contextvar the state resolvers
        read), else the deployment's own identity via stamp_owner. Delivery is
        ownership.may_observe against the identity set resolved at connect
        (client_identities): strict membership for hosted strangers, everything for the
        machine's own human — desktop is the degenerate case where both sides are "local".
        A socket the map does not know observes nothing owned: fail closed, not open.

        Agent-SCOPED app connections additionally only receive what _scoped_event_allowed
        permits (their own agent's events). The frame is parsed lazily, once, and only when
        a scoped connection actually exists — host-only deployments pay nothing."""
        owner = accounts.account_id() or ownership.stamp_owner(None, self._hosted())
        dead = []
        scoped_meta: tuple[str, dict] | None = None
        for ws in self.clients:
            if not ownership.may_observe(owner, self.client_identities.get(ws, frozenset())):
                continue
            scope = self.client_scopes.get(ws)
            if scope is not None:
                if scoped_meta is None:
                    try:
                        obj = json.loads(frame)
                        scoped_meta = (str(obj.get("event") or ""), obj.get("payload") or {})
                    except ValueError:
                        scoped_meta = ("", {})
                if not _scoped_event_allowed(scoped_meta[0], scoped_meta[1], scope):
                    continue
            try:
                await ws.send(frame)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)
            self.client_scopes.pop(ws, None)
            self.client_identities.pop(ws, None)

    # ---------------------------------------------------------- notifications

    def _build_notifier(self) -> None:
        """Compose the outbound notify channels (client-push + durable store). Default
        on; AGENTD_NOTIFY=0 disables it. The task_store doubles as the NotifyStore."""
        if self.notifier is not None or not getattr(self.config, "notify_enabled", True):
            return
        from agent_runtime.infrastructure.notify import build_notifier

        self.notifier = build_notifier(
            self.task_store, self._push_notification, extra=self.channel_notifiers
        )

    async def _push_notification(self, n: Notification) -> None:
        """Broadcast a notification to connected clients (session-less, event=notification)."""
        await self._send_all(
            dump_frame(
                Event(
                    event="notification",
                    payload={
                        "id": n.id,
                        "agentId": n.agent_id,
                        "kind": n.kind,
                        "text": n.text,
                        "detail": n.detail,
                        "at": datetime.fromtimestamp(n.created_at or time.time()).strftime(
                            "%Y-%m-%d %H:%M"
                        ),
                    },
                )
            )
        )

    async def _notify_run(self, handle: RunHandle, status: str, detail: str) -> None:
        """A scheduled run ended blocked/failed -> notify the user (5a)."""
        agent_id = agent_id_from_session_key(handle.session_key)
        n = Notification(
            id=uuid.uuid4().hex[:12],
            agent_id=agent_id,
            kind=status,
            text=f"{agent_id} — scheduled run {status}",
            detail=detail,
            created_at=time.time(),
        )
        try:
            await self.notifier.notify(n)
        except Exception:  # noqa: BLE001 — notify must never break the run
            log.warning("notify failed", exc_info=True)

    def _notifications_list(self, params: dict) -> dict:
        if self.task_store is None:
            return {"autonomy": False, "notifications": []}
        try:
            limit = max(1, min(int(params.get("limit", 50)), 500))
        except (TypeError, ValueError):
            limit = 50
        ns = self.task_store.notifications(
            agent_id=(params.get("agentId") or "").strip() or None,
            unread_only=bool(params.get("unread", False)),
            limit=limit,
        )
        return {
            "autonomy": True,
            "notifications": [
                {
                    "id": n.id,
                    "agentId": n.agent_id,
                    "kind": n.kind,
                    "text": n.text,
                    "detail": n.detail,
                    "read": n.read,
                    "at": datetime.fromtimestamp(n.created_at).strftime("%Y-%m-%d %H:%M"),
                }
                for n in ns
            ],
        }

    def _notifications_ack(self, params: dict) -> dict:
        if self.task_store is None:
            return {"acked": 0}
        nid = (params.get("id") or "").strip()
        if nid in ("*", "all"):  # ack everything unread
            acked = sum(
                1
                for n in self.task_store.notifications(unread_only=True, limit=1000)
                if self.task_store.ack(n.id)
            )
            return {"acked": acked}
        return {"acked": int(bool(nid and self.task_store.ack(nid))), "id": nid}

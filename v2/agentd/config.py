"""Configuration: optional JSON file + environment overrides.

Env vars: AGENTD_MODEL, AGENTD_HOST, AGENTD_PORT, AGENTD_WORKSPACE,
AGENTD_STATE_DIR, AGENTD_HEADLESS, AGENTD_SEARCH_PROVIDERS, BRAVE_API_KEY.
Provider API keys (ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY, ...) are
read by LiteLLM directly from the environment.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

# v2 project root (this file is v2/agentd/config.py). Everything agentd reads or
# writes is anchored here so v2 is fully self-contained — it never reaches outside.
V2_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class McpServerConfig:
    """One external MCP server to connect to (JSON config only).

    stdio: set `command` (and optional `env`) — agentd launches it as a subprocess.
    http (later phase): set `url` (and optional `headers`). `allow` optionally
    restricts which of the server's tools are exposed.
    """
    name: str                                   # namespace, e.g. "google"
    transport: str = "stdio"                    # "stdio" | "http"
    command: list | None = None                 # stdio: ["uvx", "workspace-mcp", ...]
    env: dict | None = None                     # stdio: extra env for the subprocess
    url: str | None = None                      # http: server endpoint
    headers: dict | None = None                 # http: auth headers
    enabled: bool = True
    allow: list | None = None                   # optional tool allowlist (bare names)


@dataclass
class Config:
    # The agent's persona name (how it introduces itself + identifies in the prompt).
    # Single source of truth: the server owns it; clients fetch it via the `hello`
    # handshake. Override with AGENTD_AGENT_NAME.
    agent_name: str = "JARVIS"
    model: str = "gemini/gemini-3.1-pro-preview"
    # Dedicated model for web_search grounding — kept FAST + cheap, decoupled from the
    # main model. Grounding on a heavy reasoning model (e.g. 3.1-pro ~33s) blows the
    # web_search timeout; flash grounds in ~6s. Override with AGENTD_SEARCH_MODEL.
    search_model: str = "gemini/gemini-2.5-flash"
    reasoning_effort: str = "medium"  # off | low | medium | high (LiteLLM reasoning_effort)
    host: str = "127.0.0.1"
    port: int = 8787
    # Where file/exec tools operate. Defaults to the user's home so the agent can
    # reach personal files ("read my CV"); override with AGENTD_WORKSPACE for a
    # project-scoped (coding) workspace.
    workspace: Path = field(default_factory=Path.home)
    state_dir: Path = field(default_factory=lambda: V2_ROOT / ".agentd")
    # Folder of loadable skills (each subfolder holds a SKILL.md playbook). The
    # agent reads a skill on demand when a task matches its description. Drop new
    # skills here; override with AGENTD_SKILLS_DIR.
    skills_dir: Path = field(default_factory=lambda: V2_ROOT / "skills")
    # Folder of agent DEFINITIONS — each `agents/<id>/` holds an optional agent.toml
    # (model, tool allow/deny, skill allowlist, workspace, heartbeat) + bootstrap
    # markdown (IDENTITY/AGENTS/USER/MEMORY) + skills/. The single-agent app is just
    # the `main` agent synthesized from this config; drop a new `agents/<id>/` dir to
    # add an independent agent. Override with AGENTD_AGENTS_DIR.
    agents_dir: Path = field(default_factory=lambda: V2_ROOT / "agents")
    brave_api_key: str | None = None
    # Parallel's hosted Search MCP (https://search.parallel.ai/mcp) — the keyless,
    # streamable-HTTP search backend OpenClaw uses as its zero-config default. Free
    # tier needs NO key; PARALLEL_API_KEY only raises rate limits. AGENTD_PARALLEL_SEARCH=0
    # to disable.
    parallel_search_enabled: bool = True
    parallel_search_url: str = "https://search.parallel.ai/mcp"
    parallel_api_key: str | None = None
    # Explicit web_search provider chain order (e.g. ["parallel","duckduckgo"]).
    # None = auto: matches OpenClaw's no-keys default — parallel (keyless Search MCP)
    # -> duckduckgo. Gemini/Brave stay available but, like OpenClaw, are not auto-on.
    # Override with AGENTD_SEARCH_PROVIDERS (comma-separated).
    search_providers: list[str] | None = None
    browser_headless: bool = True
    # Persistent browser profile: keep cookies/logins on disk (<state_dir>/browser-profile)
    # so the `browser` tool stays SIGNED IN across runs. Log in once (headed) via
    # `python -m agentd.main.browser_login`. AGENTD_BROWSER_PERSISTENT=0 to disable.
    browser_persistent: bool = True
    # CDP-attach: when set, the browser tool drives the user's ALREADY-RUNNING
    # Chromium over the DevTools protocol instead of launching its own (so it uses
    # the user's live profile/cookies). Start Chrome with
    # `--remote-debugging-port=9222` and set AGENTD_BROWSER_CDP_URL=http://localhost:9222.
    # Empty = launch our own Playwright browser (the default). A session can't switch
    # browsers mid-run (see interfaces/browser.py), so this is chosen once at startup.
    browser_cdp_url: str | None = None
    # Capture file downloads triggered in the browser into <state_dir>/downloads.
    # AGENTD_BROWSER_DOWNLOADS=0 to disable.
    browser_downloads: bool = True
    # Use the INSTALLED Google Chrome ("chrome") instead of Playwright's bundled
    # Chromium. Real Chrome is far less likely to be blocked by sites' automation
    # detection (e.g. Google's "this browser is not secure" on login). Falls back to
    # bundled Chromium if Chrome isn't installed. Set "" to force bundled. AGENTD_BROWSER_CHANNEL.
    browser_channel: str = "chrome"
    # Strip Playwright's automation fingerprints (--enable-automation,
    # navigator.webdriver) so logins on automation-sensitive sites work.
    # AGENTD_BROWSER_STEALTH=0 to disable.
    browser_stealth: bool = True
    # --- browser ENGINE selection (two independent toggles) --------------------
    # Default = our built-in Playwright/CDP engine. To use the external Vercel
    # `agent-browser` engine (via its MCP server) instead, turn ours OFF and
    # agent-browser ON. Rules: ours wins if BOTH are on; ours is used if BOTH are
    # off (never lose the browser). agent-browser must be installed separately
    # (`npm i -g agent-browser && agent-browser install`); configure its behaviour
    # with its own AGENT_BROWSER_* env vars (e.g. AGENT_BROWSER_PROFILE, _HEADED).
    browser_engine_playwright: bool = True       # AGENTD_BROWSER_PLAYWRIGHT
    browser_engine_agent_browser: bool = False   # AGENTD_BROWSER_AGENT_BROWSER
    # Command that launches agent-browser's MCP server (stdio). Default: ["agent-browser","mcp"].
    agent_browser_command: list | None = None    # AGENTD_AGENT_BROWSER_COMMAND (space-separated)
    # Surface non-ARIA clickable elements (cursor:pointer / onclick / tabindex /
    # contenteditable) in snapshots with [ref=cN], so the agent can target clickable
    # <div>s and styled controls the accessibility tree misses (ported from
    # agent-browser's cursor scan). AGENTD_BROWSER_CURSOR_SCAN=0 to disable.
    browser_cursor_scan: bool = True
    # Seed the browser profile from an existing CHROME profile (login reuse): a profile
    # dir name ("Default", "Profile 1"), its display name, or an absolute path. Copied
    # ONCE (cookies/logins, caches excluded) into <state_dir>/browser-profile-imported.
    # Close Chrome first for a clean copy (the live Cookies DB is locked while it runs).
    # AGENTD_BROWSER_CHROME_PROFILE.
    browser_chrome_profile: str | None = None
    # How many recent console messages per tab the `console` action can return.
    browser_console_buffer: int = 200
    # Default per-action timeout (ms) for click/fill/select/hover/etc. Playwright's
    # own default is 30s, which makes a covered/stale element hang the whole flow for
    # 30s; a shorter cap fails fast so the agent can re-snapshot and recover.
    # AGENTD_BROWSER_ACTION_TIMEOUT (ms).
    browser_action_timeout_ms: int = 12000
    exec_timeout_sec: int = 1800
    max_turns: int = 100  # agent-loop iteration cap (LLM turns per run); override AGENTD_MAX_TURNS
    agent_id: str = "main"

    # --- reliability / guardrails (applied to EVERY tool via GuardedTool) -------
    # Per-tool effective values resolve: tool_overrides[name] > the tool's own
    # declared default (default_* class attr) > these globals.
    tool_timeout_default: float = 300.0   # wall-clock per tool call (AGENTD_TOOL_TIMEOUT); per-tool null = no wrapper
    tool_retries_default: int = 0         # extra attempts on transient errors (AGENTD_TOOL_RETRIES)
    # Loop detection (same GuardedTool chokepoint; per-tool overridable via tool_overrides):
    # block a call repeated with IDENTICAL args more than N times in a row (0 = off),
    # and append a "stop retrying / switch tools" nudge after N consecutive errors (0 = off).
    tool_loop_max_repeats_default: int = 5
    tool_loop_warn_after_errors_default: int = 4
    # Per-tool overrides, e.g. {"computer": {"timeout_sec": 900}, "exec": {"timeout_sec": null},
    # "web_search": {"timeout_sec": 20, "max_retries": 3, "retryable": true}}. JSON config only.
    tool_overrides: dict = field(default_factory=dict)
    # Loop/LLM-level timeouts.
    llm_idle_timeout_seconds: float = 120.0    # abort a model stream silent for this long (AGENTD_LLM_IDLE_TIMEOUT)
    llm_request_timeout_seconds: float = 600.0  # hard ceiling per model call (AGENTD_LLM_REQUEST_TIMEOUT)

    # --- quality + liveness (decoupled seams; all default OFF => unchanged behavior) ---
    # Liveness observers that detect a stuck/looping run, comma-separated.
    # Options: callrate (varying-arg flail), noprogress (no new info N turns). AGENTD_LIVENESS.
    liveness: list[str] | None = None
    # The agent-invoked `verify_answer` TOOL (the agent reviews its own draft before
    # replying). OFF => the tool is not registered at all — exactly as if it never existed.
    verify_tool: bool = False                  # AGENTD_VERIFY_TOOL
    verify_model: str | None = None            # judge model for the tool (defaults to search_model -> model); AGENTD_VERIFY_MODEL
    # Include the in-band "## Before You Finish" honesty/completeness self-check. ON by
    # default (S3 — honesty by default): the agent must back claims with real evidence and
    # never fabricate. AGENTD_COMPLETENESS_CHECK=0 to disable.
    completeness_check: bool = True            # AGENTD_COMPLETENESS_CHECK
    # Default agent PERSONA/disposition. Loaded from the editable SOUL.md (persona_file)
    # with a built-in fallback; an agent's IDENTITY can override its tone. AGENTD_PERSONA=0.
    persona_enabled: bool = True               # AGENTD_PERSONA
    persona_file: str | None = None            # path to SOUL.md; default set in load_config; AGENTD_PERSONA_FILE
    # Long-term memory (Phase 3): when on, the agent gets remember/memory_search/memory_get
    # tools backed by a durable bank (<state_dir>/memory.sqlite) it can recall across
    # sessions. OFF by default (additive; AGENTD_MEMORY=1 to enable).
    memory_enabled: bool = False               # AGENTD_MEMORY
    # Context compaction (Phase 3.5 / S7): cap the message history sent to the model to the
    # most-recent N (boundary-safe truncation). 0 = off (send everything). AGENTD_CONTEXT_MAX.
    context_max_messages: int = 0
    # Sub-agents (Phase 4a / S8): the agent can delegate a subtask to a fresh child run via
    # `spawn_subagent` and get its result back. OFF by default; AGENTD_SUBAGENTS=1 to enable.
    subagents_enabled: bool = False            # AGENTD_SUBAGENTS
    subagent_max: int = 4                      # max concurrent child runs (runaway guard)
    # skill_workshop (S10): the agent authors reusable SKILL.md playbooks at runtime.
    # OFF by default; AGENTD_SKILL_WORKSHOP=1 to enable.
    skill_workshop: bool = False               # AGENTD_SKILL_WORKSHOP
    # Model failover (S11): models to try, in order, when the primary errors before any
    # output. Empty = no failover. AGENTD_MODEL_FALLBACKS=comma,separated,ids.
    model_fallbacks: list = field(default_factory=list)
    # Execution sandbox (S17, seam): "" / "local" = run on host (default, unchanged);
    # "docker"/"ssh" select an isolating adapter (not yet implemented). AGENTD_SANDBOX.
    sandbox: str = ""

    # --- autonomy (heartbeat) — Phase 2 ----------------------------------------
    # OFF by default. When on, the shared scheduler wakes each agent that declares a
    # `heartbeat` interval (in its agent.toml) to read its HEARTBEAT.md and act on a
    # tick. The reactive chat path is unchanged either way. AGENTD_AUTONOMY=1 to enable.
    autonomy_enabled: bool = False
    heartbeat_default_interval: str = ""       # e.g. "30m"; per-agent agent.toml overrides
    heartbeat_active_hours: str = ""           # e.g. "08:00-22:00" (empty = always)
    # Outbound notifications (Phase 5a): the gateway reaches the user when a scheduled
    # run ends blocked/failed (client-push + durable). On by default; AGENTD_NOTIFY=0
    # disables. Only ever fires under autonomy (no cron runs => nothing to notify).
    notify_enabled: bool = True
    # RUN seam (reliability): a scheduled run that finishes without the agent declaring an
    # outcome (report_outcome) is recorded as `incomplete`, not a silent `ok` — so a run
    # can't claim success it never verified. AGENTD_ENFORCE_OUTCOME=0 to cut this layer.
    enforce_outcome: bool = True
    # TURN seam (workspace awareness): inject a manifest of the agent's workspace files
    # (scripts/docs/images/data) into every prompt so resources it created are always
    # visible and reusable. AGENTD_WORKSPACE_INDEX=0 to cut this layer.
    workspace_index_enabled: bool = True
    workspace_index_max_files: int = 100       # cap the manifest (AGENTD_WORKSPACE_INDEX_MAX)
    # Resource Manager (OpenClaw-style): a cached, DESCRIBED index of workspace resources
    # (scripts/docs/images/data) + a `resource` CRUD tool. Supersedes the plain workspace
    # index for the manifest when on. AGENTD_RESOURCES=0 to cut this layer.
    resource_manager_enabled: bool = True
    resource_index_max_files: int = 100        # cap the index (AGENTD_RESOURCES_MAX)
    # Optional Gemini VISION captions for image resources, computed in the manager's
    # BACKGROUND (never on the agent's path). OFF by default: it sends image bytes to
    # Google + costs API calls. AGENTD_RESOURCE_VISION=1 to enable.
    resource_vision_enabled: bool = False             # caption images  (AGENTD_RESOURCE_VISION)
    # LLM one-line SUMMARIES for text resources (scripts/docs/data + extracted docx/pdf/xlsx),
    # computed in the BACKGROUND. OFF by default: it sends file content to Google + costs API
    # calls. AGENTD_RESOURCE_SUMMARIZE=1. Both tiers reuse the same model/timeout below.
    resource_summarize_enabled: bool = False          # summarize text  (AGENTD_RESOURCE_SUMMARIZE)
    resource_vision_model: str = "gemini-2.5-flash"   # AGENTD_RESOURCE_VISION_MODEL (used for both)
    resource_vision_timeout_seconds: float = 60.0
    # Messaging channels (Phase 5b): JSON list of channel configs (default none => off).
    # Each: {type: email|memory, agent, notify_to?, poll_query?, *_tool?}. A channel
    # polls for inbound messages -> runs its agent -> replies on the same channel; one
    # with `notify_to` also delivers notifications there (reuses the one transport).
    channels: list = field(default_factory=list)
    channel_poll_seconds: float = 15.0         # inbound poll cadence (AGENTD_CHANNEL_POLL)

    # --- MCP servers (external tool connectors) --------------------------------
    # List of McpServerConfig (JSON config only). Empty = MCP off. Each server's
    # tools are discovered at startup and namespaced as "<name>__<tool>".
    mcp_servers: list = field(default_factory=list)

    # --- computer-use (PC GUI automation) tool ---------------------------------
    # OFF by default: this tool drives the REAL mouse/keyboard/screen, so it ships
    # disabled and is only registered when AGENTD_COMPUTER_ENABLED=1. Every step
    # sends a full-screen screenshot to `computer_model` (may contain sensitive
    # on-screen data) — point it at a trusted/Vertex endpoint for sensitive use.
    computer_enabled: bool = False
    # The vision model that drives the see->click loop (decoupled from `model`;
    # the main agent can be any LLM). Default = Gemini's dedicated computer-use
    # model, driven via google-genai. Override with AGENTD_COMPUTER_MODEL.
    computer_model: str = "gemini-2.5-computer-use-preview-10-2025"
    computer_max_steps: int = 25       # loop step cap; override AGENTD_COMPUTER_MAX_STEPS
    computer_send_max: int = 1440      # longest screenshot side sent to the model (Gemini docs recommend ~1440x900); AGENTD_COMPUTER_SEND_MAX
    computer_capture: str = "primary"  # primary | virtual (multi-monitor, best-effort)
    computer_pause: float = 0.15       # pyautogui inter-action settle delay (seconds)
    computer_call_timeout_seconds: float = 120.0  # per model call in the driver (AGENTD_COMPUTER_CALL_TIMEOUT)
    # DEV ONLY: persist each step's screenshot to state_dir/screenshots/computer-<ts>/
    # for inspection. OFF by default (privacy + unbounded disk growth). Turn on while
    # developing with AGENTD_COMPUTER_SAVE_SCREENSHOTS=1, off again when done.
    computer_save_screenshots: bool = False
    # Multi-monitor: when capturing the primary monitor, pull any window that opens
    # on another monitor back onto the primary (captured) one + maximize, so the
    # tool can see it. Windows-only, best-effort. AGENTD_COMPUTER_CORRAL=0 to disable.
    computer_corral_to_primary: bool = True


def _dotenv_value(raw: str) -> str:
    """Parse a raw .env value: strip surrounding quotes and a trailing inline comment.

    Conventions (match common dotenv parsers), so a commented line like
    ``AGENTD_NOTIFY=1   # on`` yields ``1`` and not ``1   # on``:
      * a quoted value (``"..."`` / ``'...'``) is taken verbatim — a ``#`` inside is literal
      * otherwise an inline comment starts at the first whitespace-then-``#`` (`` #`` / ``\\t#``)
      * a leading ``#`` (e.g. ``#fff``) is kept — only a ``#`` AFTER whitespace is a comment
    """
    value = raw.strip()
    if value[:1] in ("'", '"'):
        quote = value[0]
        end = value.find(quote, 1)
        return value[1:end] if end != -1 else value[1:]
    cuts = [i for i in (value.find(" #"), value.find("\t#")) if i != -1]
    if cuts:
        value = value[:min(cuts)]
    return value.rstrip()


def _load_dotenv() -> None:
    """Load KEY=VALUE lines from v2's own .env into os.environ (no override).

    Anchored to V2_ROOT (not the cwd or any parent) so agentd uses only v2/.env
    and never depends on a .env outside the v2 folder.
    """
    env_path = V2_ROOT / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), _dotenv_value(value)
        if key and value and key not in os.environ:
            os.environ[key] = value


def load_config(path: Path | None = None) -> Config:
    _load_dotenv()
    cfg = Config()

    candidates = [path] if path else [
        Path(os.environ.get("AGENTD_CONFIG", "")) if os.environ.get("AGENTD_CONFIG") else None,
        Path("agentd.config.json"),
        V2_ROOT / "agentd.config.json",
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            data = json.loads(candidate.read_text(encoding="utf-8"))
            for key, value in data.items():
                if hasattr(cfg, key):
                    if key in ("workspace", "state_dir", "skills_dir", "agents_dir"):
                        value = Path(value)
                    setattr(cfg, key, value)
            break

    if os.environ.get("AGENTD_AGENT_NAME"):
        cfg.agent_name = os.environ["AGENTD_AGENT_NAME"]
    if os.environ.get("AGENTD_MODEL"):
        cfg.model = os.environ["AGENTD_MODEL"]
    if os.environ.get("AGENTD_SEARCH_MODEL"):
        cfg.search_model = os.environ["AGENTD_SEARCH_MODEL"]
    if os.environ.get("AGENTD_REASONING"):
        cfg.reasoning_effort = os.environ["AGENTD_REASONING"]
    if os.environ.get("AGENTD_HOST"):
        cfg.host = os.environ["AGENTD_HOST"]
    if os.environ.get("AGENTD_PORT"):
        cfg.port = int(os.environ["AGENTD_PORT"])
    if os.environ.get("AGENTD_MAX_TURNS"):
        cfg.max_turns = int(os.environ["AGENTD_MAX_TURNS"])
    if os.environ.get("AGENTD_WORKSPACE"):
        cfg.workspace = Path(os.environ["AGENTD_WORKSPACE"])
    if os.environ.get("AGENTD_STATE_DIR"):
        cfg.state_dir = Path(os.environ["AGENTD_STATE_DIR"])
    if os.environ.get("AGENTD_SKILLS_DIR"):
        cfg.skills_dir = Path(os.environ["AGENTD_SKILLS_DIR"])
    if os.environ.get("AGENTD_AGENTS_DIR"):
        cfg.agents_dir = Path(os.environ["AGENTD_AGENTS_DIR"])
    if os.environ.get("AGENTD_HEADLESS"):
        cfg.browser_headless = os.environ["AGENTD_HEADLESS"].lower() not in ("0", "false", "no")
    if os.environ.get("AGENTD_BROWSER_PERSISTENT"):
        cfg.browser_persistent = (
            os.environ["AGENTD_BROWSER_PERSISTENT"].lower() not in ("0", "false", "no", "")
        )
    if os.environ.get("AGENTD_BROWSER_CDP_URL"):
        cfg.browser_cdp_url = os.environ["AGENTD_BROWSER_CDP_URL"].strip() or None
    if os.environ.get("AGENTD_BROWSER_DOWNLOADS"):
        cfg.browser_downloads = (
            os.environ["AGENTD_BROWSER_DOWNLOADS"].lower() not in ("0", "false", "no", "")
        )
    if os.environ.get("AGENTD_BROWSER_CHANNEL") is not None:
        cfg.browser_channel = os.environ["AGENTD_BROWSER_CHANNEL"].strip()
    if os.environ.get("AGENTD_BROWSER_STEALTH"):
        cfg.browser_stealth = (
            os.environ["AGENTD_BROWSER_STEALTH"].lower() not in ("0", "false", "no", "")
        )
    if os.environ.get("AGENTD_BROWSER_PLAYWRIGHT"):
        cfg.browser_engine_playwright = (
            os.environ["AGENTD_BROWSER_PLAYWRIGHT"].lower() not in ("0", "false", "no", "")
        )
    if os.environ.get("AGENTD_BROWSER_AGENT_BROWSER"):
        cfg.browser_engine_agent_browser = (
            os.environ["AGENTD_BROWSER_AGENT_BROWSER"].lower() not in ("0", "false", "no", "")
        )
    if os.environ.get("AGENTD_AGENT_BROWSER_COMMAND"):
        cfg.agent_browser_command = os.environ["AGENTD_AGENT_BROWSER_COMMAND"].split()
    if os.environ.get("AGENTD_BROWSER_CURSOR_SCAN"):
        cfg.browser_cursor_scan = (
            os.environ["AGENTD_BROWSER_CURSOR_SCAN"].lower() not in ("0", "false", "no", "")
        )
    if os.environ.get("AGENTD_BROWSER_CHROME_PROFILE"):
        cfg.browser_chrome_profile = os.environ["AGENTD_BROWSER_CHROME_PROFILE"].strip() or None
    if os.environ.get("AGENTD_BROWSER_ACTION_TIMEOUT"):
        cfg.browser_action_timeout_ms = int(os.environ["AGENTD_BROWSER_ACTION_TIMEOUT"])
    if os.environ.get("BRAVE_API_KEY"):
        cfg.brave_api_key = os.environ["BRAVE_API_KEY"]
    if os.environ.get("AGENTD_AUTONOMY") is not None:
        cfg.autonomy_enabled = os.environ["AGENTD_AUTONOMY"].lower() in ("1", "true", "yes", "on")
    if os.environ.get("AGENTD_NOTIFY"):
        cfg.notify_enabled = os.environ["AGENTD_NOTIFY"].lower() in ("1", "true", "yes", "on")
    if os.environ.get("AGENTD_ENFORCE_OUTCOME"):
        cfg.enforce_outcome = os.environ["AGENTD_ENFORCE_OUTCOME"].lower() not in ("0", "false", "no", "")
    if os.environ.get("AGENTD_WORKSPACE_INDEX"):
        cfg.workspace_index_enabled = os.environ["AGENTD_WORKSPACE_INDEX"].lower() not in ("0", "false", "no", "")
    if os.environ.get("AGENTD_WORKSPACE_INDEX_MAX"):
        cfg.workspace_index_max_files = int(os.environ["AGENTD_WORKSPACE_INDEX_MAX"])
    if os.environ.get("AGENTD_RESOURCES"):
        cfg.resource_manager_enabled = os.environ["AGENTD_RESOURCES"].lower() not in ("0", "false", "no", "")
    if os.environ.get("AGENTD_RESOURCES_MAX"):
        cfg.resource_index_max_files = int(os.environ["AGENTD_RESOURCES_MAX"])
    if os.environ.get("AGENTD_RESOURCE_VISION"):
        cfg.resource_vision_enabled = os.environ["AGENTD_RESOURCE_VISION"].lower() not in ("0", "false", "no", "")
    if os.environ.get("AGENTD_RESOURCE_SUMMARIZE"):
        cfg.resource_summarize_enabled = os.environ["AGENTD_RESOURCE_SUMMARIZE"].lower() not in ("0", "false", "no", "")
    if os.environ.get("AGENTD_RESOURCE_VISION_MODEL"):
        cfg.resource_vision_model = os.environ["AGENTD_RESOURCE_VISION_MODEL"]
    if os.environ.get("AGENTD_CHANNEL_POLL"):
        cfg.channel_poll_seconds = float(os.environ["AGENTD_CHANNEL_POLL"])
    if os.environ.get("AGENTD_HEARTBEAT_INTERVAL"):
        cfg.heartbeat_default_interval = os.environ["AGENTD_HEARTBEAT_INTERVAL"]
    if os.environ.get("AGENTD_HEARTBEAT_HOURS"):
        cfg.heartbeat_active_hours = os.environ["AGENTD_HEARTBEAT_HOURS"]
    if os.environ.get("AGENTD_PARALLEL_SEARCH") is not None:
        cfg.parallel_search_enabled = os.environ["AGENTD_PARALLEL_SEARCH"].lower() in ("1", "true", "yes", "on")
    if os.environ.get("PARALLEL_API_KEY"):
        cfg.parallel_api_key = os.environ["PARALLEL_API_KEY"]
    if os.environ.get("AGENTD_SEARCH_PROVIDERS"):
        cfg.search_providers = [
            s.strip() for s in os.environ["AGENTD_SEARCH_PROVIDERS"].split(",") if s.strip()
        ]
    if os.environ.get("AGENTD_COMPUTER_ENABLED"):
        cfg.computer_enabled = os.environ["AGENTD_COMPUTER_ENABLED"].lower() not in ("0", "false", "no", "")
    if os.environ.get("AGENTD_COMPUTER_MODEL"):
        cfg.computer_model = os.environ["AGENTD_COMPUTER_MODEL"]
    if os.environ.get("AGENTD_COMPUTER_MAX_STEPS"):
        cfg.computer_max_steps = int(os.environ["AGENTD_COMPUTER_MAX_STEPS"])
    if os.environ.get("AGENTD_COMPUTER_CAPTURE"):
        cfg.computer_capture = os.environ["AGENTD_COMPUTER_CAPTURE"]
    if os.environ.get("AGENTD_COMPUTER_SEND_MAX"):
        cfg.computer_send_max = int(os.environ["AGENTD_COMPUTER_SEND_MAX"])
    if os.environ.get("AGENTD_COMPUTER_SAVE_SCREENSHOTS"):
        cfg.computer_save_screenshots = (
            os.environ["AGENTD_COMPUTER_SAVE_SCREENSHOTS"].lower() not in ("0", "false", "no", "")
        )
    if os.environ.get("AGENTD_COMPUTER_CORRAL"):
        cfg.computer_corral_to_primary = (
            os.environ["AGENTD_COMPUTER_CORRAL"].lower() not in ("0", "false", "no", "")
        )
    if os.environ.get("AGENTD_COMPUTER_CALL_TIMEOUT"):
        cfg.computer_call_timeout_seconds = float(os.environ["AGENTD_COMPUTER_CALL_TIMEOUT"])
    if os.environ.get("AGENTD_TOOL_TIMEOUT"):
        cfg.tool_timeout_default = float(os.environ["AGENTD_TOOL_TIMEOUT"])
    if os.environ.get("AGENTD_TOOL_RETRIES"):
        cfg.tool_retries_default = int(os.environ["AGENTD_TOOL_RETRIES"])
    if os.environ.get("AGENTD_LLM_IDLE_TIMEOUT"):
        cfg.llm_idle_timeout_seconds = float(os.environ["AGENTD_LLM_IDLE_TIMEOUT"])
    if os.environ.get("AGENTD_LLM_REQUEST_TIMEOUT"):
        cfg.llm_request_timeout_seconds = float(os.environ["AGENTD_LLM_REQUEST_TIMEOUT"])
    if os.environ.get("AGENTD_LIVENESS"):
        cfg.liveness = [s.strip() for s in os.environ["AGENTD_LIVENESS"].split(",") if s.strip()]
    if os.environ.get("AGENTD_VERIFY_TOOL"):
        cfg.verify_tool = os.environ["AGENTD_VERIFY_TOOL"].lower() not in ("0", "false", "no", "")
    if os.environ.get("AGENTD_VERIFY_MODEL"):
        cfg.verify_model = os.environ["AGENTD_VERIFY_MODEL"]
    if os.environ.get("AGENTD_COMPLETENESS_CHECK"):
        cfg.completeness_check = (
            os.environ["AGENTD_COMPLETENESS_CHECK"].lower() not in ("0", "false", "no", "")
        )
    if os.environ.get("AGENTD_PERSONA"):
        cfg.persona_enabled = os.environ["AGENTD_PERSONA"].lower() not in ("0", "false", "no", "")
    cfg.persona_file = os.environ.get("AGENTD_PERSONA_FILE") or cfg.persona_file \
        or str(Path(cfg.state_dir).parent / "SOUL.md")   # default: the repo-root SOUL.md (editable)
    if os.environ.get("AGENTD_MEMORY"):
        cfg.memory_enabled = os.environ["AGENTD_MEMORY"].lower() not in ("0", "false", "no", "")
    if os.environ.get("AGENTD_CONTEXT_MAX"):
        cfg.context_max_messages = int(os.environ["AGENTD_CONTEXT_MAX"])
    if os.environ.get("AGENTD_SUBAGENTS"):
        cfg.subagents_enabled = os.environ["AGENTD_SUBAGENTS"].lower() not in ("0", "false", "no", "")
    if os.environ.get("AGENTD_SKILL_WORKSHOP"):
        cfg.skill_workshop = os.environ["AGENTD_SKILL_WORKSHOP"].lower() not in ("0", "false", "no", "")
    if os.environ.get("AGENTD_MODEL_FALLBACKS"):
        cfg.model_fallbacks = [s.strip() for s in os.environ["AGENTD_MODEL_FALLBACKS"].split(",") if s.strip()]

    # mcp_servers come from JSON as plain dicts; coerce to typed McpServerConfig.
    cfg.mcp_servers = [
        s if isinstance(s, McpServerConfig) else McpServerConfig(**s)
        for s in (cfg.mcp_servers or [])
        if isinstance(s, (dict, McpServerConfig))
    ]

    cfg.workspace = Path(cfg.workspace).resolve()
    cfg.state_dir = Path(cfg.state_dir)
    cfg.skills_dir = Path(cfg.skills_dir)
    return cfg


def resolve_browser_engine(config) -> str:
    """Decide the browser engine from the two toggles.

    Returns "playwright" (our built-in engine) or "agent_browser" (external, via
    its MCP server). Ours is the default and wins ties: agent-browser is used ONLY
    when ours is explicitly OFF and agent-browser is ON. Both off => ours, so the
    browser is never accidentally lost.
    """
    use_pw = getattr(config, "browser_engine_playwright", True)
    use_ab = getattr(config, "browser_engine_agent_browser", False)
    if use_pw:
        return "playwright"
    if use_ab:
        return "agent_browser"
    return "playwright"

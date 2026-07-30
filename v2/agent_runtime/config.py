"""Configuration: optional JSON file + environment overrides.

Env vars: AGENTD_MODEL, AGENTD_HOST, AGENTD_PORT, AGENTD_WORKSPACE,
AGENTD_STATE_DIR, AGENTD_HEADLESS, BRAVE_API_KEY.
Provider API keys (ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY, ...) are
read by LiteLLM directly from the environment.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from agent_runtime import runtime_paths
from agent_runtime.distribution import DistributionProfile, load_profile

# v2 project root (this file is v2/agent_runtime/config.py). In a checkout everything agentd
# reads or writes anchors here so v2 is fully self-contained. When INSTALLED as a wheel
# this is site-packages — which is why every default below resolves through
# runtime_paths (repo mode: unchanged; packaged mode: ~/.agentd + in-package built-ins).
V2_ROOT = runtime_paths.REPO_ROOT


@dataclass
class McpServerConfig:
    """One external MCP server to connect to (JSON config only).

    stdio: set `command` (and optional `env`) — agentd launches it as a subprocess.
    http (later phase): set `url` (and optional `headers`). `allow` optionally
    restricts which of the server's tools are exposed.
    """

    name: str  # namespace, e.g. "google"
    transport: str = "stdio"  # "stdio" | "http"
    command: list | None = None  # stdio: ["uvx", "workspace-mcp", ...]
    env: dict | None = None  # stdio: extra env for the subprocess
    url: str | None = None  # http: server endpoint
    headers: dict | None = None  # http: auth headers
    enabled: bool = True
    allow: list | None = None  # optional tool allowlist (bare names)


@dataclass
class Config:
    # The agent's persona name (how it introduces itself + identifies in the prompt).
    # Single source of truth: the server owns it; clients fetch it via the `hello`
    # handshake. Override with AGENTD_AGENT_NAME.
    agent_name: str = "JARVIS"
    # The agentd.config.json this Config was loaded from ("" => no file found). Set by load_config,
    # NOT from JSON. The models layer (application/tool_models.py) uses it to fail LOUD ("config
    # missing") instead of silently defaulting, since models come ONLY from config.
    config_path: str = ""
    model: str = "gemini/gemini-3.1-pro-preview"
    # Curated model OPTIONS the settings UI offers as a dropdown (so users pick a model
    # by display name instead of typing a litellm id). Extend/replace it in the JSON config;
    # empty => the built-in default catalog (gateway.DEFAULT_MODEL_CATALOG). Each entry is
    # {"value": "provider/model", "label": "Human name", "group": "Provider"}.
    model_catalog: list = field(default_factory=list)
    # HOUSE default model per KIND, so a tool author just declares `model_kind` and never names a
    # model. Keys: "vision" (reads images), "image-gen" (generates images), "embedding" — override
    # any of them here to retune EVERY tool of that kind at once. Empty/absent kind falls back to the
    # KIND_DEFAULT_MODELS seed in tool_models.py; "text" is omitted on purpose (text tools use the
    # brain). A per-tool `plugins.<p>.tools.<t>.model` still wins over this.
    model_defaults: dict = field(default_factory=dict)
    # ALL model-bearing TOOLS and SUBSYSTEMS pick their model from ONE place — this clean
    # PLUGIN -> TOOL -> model hierarchy — decoupled from the agent BRAIN (`model`). (web_search
    # grounding, the verify/safe_to_send judges, resource captions/summaries, computer-use, and the
    # memory/skills embedders all resolve from here; e.g. web_search is plugins.web.tools.web_search,
    # kept FAST so a heavy reasoning model can't blow the search timeout.)
    # The ONE control panel for every plugin + tool. A plugin is just a NAMESPACE + a set of tools; all
    # knobs live on the TOOL (no plugin-level model/provider defaults — pure grouping). Shape:
    #   "plugins": {
    #     "<plugin>": {
    #       "enabled": true,                   # optional; omitted => the plugin's author default
    #       #                                    (discovery._gate). false => the whole plugin is OFF.
    #       "description": "...",              # optional, for humans (ignored at runtime)
    #       "tools": {
    #         "<tool>": {
    #           "enabled": true,               # optional; false => just this tool is dropped
    #           "description": "...",
    #           "provider": "...",             # backend/SDK/engine, where the tool has one
    #           "model": "provider/model",     # the AI model, where the tool calls one
    #           "<knob>": ...                  # any other per-tool knob (read via tool_config)
    #         }
    #       }
    #     }
    #   }
    # Every knob resolves (first hit wins): per-call arg > agent.toml plugins[P].tools[T].<knob> >
    # this map's plugins[P].tools[T].<knob> > the tool's built-in default. So agent.toml wins over
    # global config; there is NO plugin-level fallback. Model values are litellm "provider/model"
    # (bare id => gemini). CONFIG-ONLY (a knob, not a secret): there is deliberately no env override.
    plugins: dict = field(default_factory=dict)
    # (the image-gen BACKEND — gemini|fal|replicate — is a plugins knob: config plugins.figure-art.provider,
    #  resolved by resolve_tool_provider; the model/endpoint is plugins.figure-art[.tools.generate_artwork].)
    reasoning_effort: str = "medium"  # off | low | medium | high (LiteLLM reasoning_effort)
    host: str = "127.0.0.1"
    port: int = 8787
    # Where file/exec tools operate. Defaults to the user's home so the agent can
    # reach personal files ("read my CV"); override with AGENTD_WORKSPACE for a
    # project-scoped (coding) workspace.
    workspace: Path = field(default_factory=Path.home)
    state_dir: Path = field(default_factory=runtime_paths.default_state_dir)
    # The SHARED/global skills library = MAIN's skills (agents/main/skills/). Every agent
    # inherits these; a named agent adds its own private agents/<id>/skills/ on top. Drop a
    # shared SKILL.md into agents/main/skills/; override the pointer with AGENTD_SKILLS_DIR.
    skills_dir: Path = field(default_factory=runtime_paths.default_skills_dir)
    # Folder of agent DEFINITIONS — each `agents/<id>/` holds an optional agent.toml
    # (model, tool allow/deny, skill allowlist, workspace, heartbeat) + bootstrap
    # markdown (IDENTITY/AGENTS/USER/MEMORY) + skills/. The single-agent app is just
    # the `main` agent synthesized from this config; drop a new `agents/<id>/` dir to
    # add an independent agent. Override with AGENTD_AGENTS_DIR.
    agents_dir: Path = field(default_factory=runtime_paths.default_agents_dir)
    # Scratch hygiene: <workspace>/tmp/ is a sanctioned throwaway dir (never indexed/enriched);
    # files in it older than this many hours are auto-swept at turn start. 0 disables the sweep.
    scratch_ttl_hours: float = 24.0  # AGENTD_SCRATCH_TTL_HOURS
    # Durable event log (observability): record EVERY run's event stream to
    # <state_dir>/events/<agent>-<run>.jsonl, so a run's play-by-play is viewable even with NO
    # client attached (cron/channel/heartbeat/sub-agent). OFF by default; AGENTD_EVENT_LOG=1.
    # Watch live with: python -m clients.watch [agent|run|latest] -f
    event_log_enabled: bool = False  # AGENTD_EVENT_LOG
    event_log_max_runs: int = 200  # keep the most recent N run files (AGENTD_EVENT_LOG_MAX)
    brave_api_key: str | None = None
    # Parallel's hosted Search MCP (https://search.parallel.ai/mcp) — the keyless,
    # streamable-HTTP search backend OpenClaw uses as its zero-config default. Free
    # tier needs NO key; PARALLEL_API_KEY only raises rate limits. AGENTD_PARALLEL_SEARCH=0
    # to disable.
    parallel_search_enabled: bool = True
    parallel_search_url: str = "https://search.parallel.ai/mcp"
    parallel_api_key: str | None = None
    # (the web_search provider CHAIN is a plugins knob: config plugins.web.provider — a list like
    #  ["parallel","duckduckgo"], or a single name. Absent => OpenClaw's no-keys auto order.)
    # --- browser + exec tool KNOBS -> the plugins block (config-only, tool-level) ----------------
    # Every browser behavioral knob lives under plugins.browser.tools.browser.<knob> and the exec
    # timeout under plugins.shell.tools.exec.<knob> — read via tool_config (see tool_models.py:
    # browser_knob / computer_knob). Keys (built-in default): headless (True), persistent (True),
    # cdp_url (None; attach to a running Chrome at e.g. "http://localhost:9222"), downloads (True),
    # channel ("chrome" | "" for bundled Chromium), stealth (True), cursor_scan (True), chrome_profile
    # (None; seed a real Chrome profile for login reuse), console_buffer (200), action_timeout_ms
    # (12000), agent_browser_command (None => ["agent-browser","mcp"]); exec timeout_sec (1800). The
    # browser ENGINE is plugins.browser.provider ("playwright" | "agent_browser"). CONFIG-ONLY — no
    # AGENTD_* env for these (env = keys, config = knobs); per-agent-overridable via agent.toml
    # [plugins.browser.tools.browser] / [plugins.shell.tools.exec].
    max_turns: int = 100  # agent-loop iteration cap (LLM turns per run); override AGENTD_MAX_TURNS
    agent_id: str = "main"

    # --- gateway auth (M2: local clients present a bearer token) -----------------
    # The WS gateway is loopback-only but loopback is reachable by ANY local process and
    # by any webpage's JS — so serve() mints a per-start token, writes it to the
    # rendezvous file (~/.agentd/gateway.json, 0600), and _handle_conn rejects
    # connections without it. AGENTD_GATEWAY_AUTH=0 disables (tests, trusted dev box);
    # AGENTD_TOKEN pins a fixed token instead of a per-start mint.
    gateway_auth: bool = True
    gateway_token: str = ""
    # Hosted deployments: vanity hostname -> agent id ({"weather.example.com": "weather"})
    # so each curated agent lives at its OWN URL on the shared daemon — the gateway serves
    # that agent's ui/ at "/" for the aliased Host and derives the connection scope from it.
    # Empty (the default, every local install) => fully dormant. Override AGENTD_APP_HOSTS
    # with a JSON object string.
    app_hosts: dict = field(default_factory=dict)

    # --- distribution (what THIS INSTALL is) + marketplace ------------------------
    # The parsed distribution.toml (product name/flavor, provisioned plugin set, store
    # wiring) — the OPEN profile when no file exists. Loaded by load_config; NOT settable
    # from JSON (an install's identity comes from the installer, not the user config).
    distribution: DistributionProfile = field(default_factory=lambda: DistributionProfile())
    # The bundle registry (marketplace index.json): file:// path, https URL, or a bare
    # local directory. Resolution: AGENTD_REGISTRY > JSON config > distribution profile.
    registry_url: str = ""

    # --- reliability / guardrails (applied to EVERY tool via GuardedTool) -------
    # Per-tool effective values resolve: tool_overrides[name] > the tool's own
    # declared default (default_* class attr) > these globals.
    tool_timeout_default: float = (
        300.0  # wall-clock per tool call (AGENTD_TOOL_TIMEOUT); per-tool null = no wrapper
    )
    tool_retries_default: int = 0  # extra attempts on transient errors (AGENTD_TOOL_RETRIES)
    # Loop detection (same GuardedTool chokepoint; per-tool overridable via tool_overrides):
    # block a call repeated with IDENTICAL args more than N times in a row (0 = off),
    # and append a "stop retrying / switch tools" nudge after N consecutive errors (0 = off).
    tool_loop_max_repeats_default: int = 5
    tool_loop_warn_after_errors_default: int = 4
    # Per-tool overrides, e.g. {"computer": {"timeout_sec": 900}, "exec": {"timeout_sec": null},
    # "web_search": {"timeout_sec": 20, "max_retries": 3, "retryable": true}}. JSON config only.
    tool_overrides: dict = field(default_factory=dict)
    # --- tool catalog ENABLEMENT (global, uniform, decoupled) --------------------
    # Three layers (see planning/platform/tools/plugin-catalog-architecture.md):
    #  1) plugins[id]=true|false   -- per-plugin LOAD gate (off => never imported). JSON config.
    #  2) tools_enabled / tools_disabled (name or trailing-* glob) -- GLOBAL on/off applied to
    #     the WHOLE catalog (internal+plugin, native+mcp). disabled wins; enabled=[] => all.
    #  3) per-agent allow/deny lives in agents/<id>/agent.toml (already implemented).
    plugins: dict = field(default_factory=dict)  # {plugin_id: bool} load gate (JSON config)
    tools_enabled: list = field(
        default_factory=list
    )  # global allowlist ([] => all); JSON / AGENTD_TOOLS_ENABLED
    tools_disabled: list = field(
        default_factory=list
    )  # global denylist; JSON / AGENTD_TOOLS_DISABLED
    # Where drop-in plugins live (each <plugins_dir>/<id>/plugin.toml). Default <V2_ROOT>/plugins;
    # AGENTD_PLUGINS_DIR overrides. (pip plugins are found via entry-points, no dir needed.)
    plugins_dir: str = ""
    # Where the SHIPPED built-in capability bundles live (the agent's standard library: fs, shell,
    # web, browser, …). ALWAYS scanned, independently of plugins_dir, so overriding the user dir
    # never drops the built-ins. Fixed to <V2_ROOT>/plugins; not env-overridable.
    builtin_plugins_dir: str = ""
    # Skill ADVERTISEMENT budget (OpenClaw parity): the ## Skills list is capped at this many
    # entries / chars; over budget it degrades to compact (name+path) + a "+N more" note, so a
    # big skill library never floods the prompt. AGENTD_SKILLS_PROMPT_MAX / _CHARS.
    skills_prompt_max: int = 150
    skills_prompt_chars: int = 18000
    # Relevance-filtered skills (optional, post-parity): when ON, advertise only the top-K skills
    # most semantically related to the current message (embeddings), instead of the whole budgeted
    # library. OFF by default => current behavior. The embedding model is a plugins knob:
    # config plugins.skills.tools.relevance (empty => off). AGENTD_SKILLS_RELEVANCE_ENABLED.
    skills_relevance_enabled: bool = False
    skills_relevance_top_k: int = 30
    # Loop/LLM-level timeouts.
    llm_idle_timeout_seconds: float = (
        120.0  # abort a model stream silent for this long (AGENTD_LLM_IDLE_TIMEOUT)
    )
    llm_request_timeout_seconds: float = (
        600.0  # hard ceiling per model call (AGENTD_LLM_REQUEST_TIMEOUT)
    )

    # --- quality + liveness (decoupled seams; all default OFF => unchanged behavior) ---
    # Liveness observers that detect a stuck/looping run, comma-separated.
    # Options: callrate (varying-arg flail), noprogress (no new info N turns). AGENTD_LIVENESS.
    liveness: list[str] | None = None
    # The agent-invoked `verify_answer` TOOL (the agent reviews its own draft before
    # replying). OFF => the tool is not registered at all — exactly as if it never existed.
    verify_tool: bool = False  # AGENTD_VERIFY_TOOL
    # (judge model is a plugins knob: config plugins.verify.tools.verify -> search chain -> brain)
    # Include the in-band "## Before You Finish" honesty/completeness self-check. ON by
    # default (S3 — honesty by default): the agent must back claims with real evidence and
    # never fabricate. AGENTD_COMPLETENESS_CHECK=0 to disable.
    completeness_check: bool = True  # AGENTD_COMPLETENESS_CHECK
    # Execution contract (OpenClaw). "strict-agentic" forces the planning-only "stop talking,
    # act now" nudge on for EVERY model; "" (default) leaves it to the per-model gate (only the
    # Gemini family gets it). A plain conversational agent is then never nudged. AGENTD_EXECUTION_CONTRACT.
    execution_contract: str = ""
    # OUT-OF-BAND SAFE-TO-SEND GATE (privacy egress check): before a reply leaves on a PUBLIC
    # channel, an independent judge LLM checks it against the agent's OWN operating rules (its
    # AGENTS.md) and BLOCKS anything that would leak (other people's data, info the rules say
    # to withhold, etc.), replacing it with a safe message. Interactive/owner sessions are
    # NEVER gated. FAIL-CLOSED: a judge error blocks that one reply. The agent stays fully
    # capable; this only governs what may be DISCLOSED to a channel recipient.
    safe_to_send_check: bool = True  # AGENTD_SAFE_TO_SEND (0 to disable)
    # (judge model is a plugins knob: config plugins.safe_to_send.tools.safe_to_send -> verify chain)
    # Default agent PERSONA/disposition. Loaded from the editable SOUL.md (persona_file)
    # with a built-in fallback; an agent's IDENTITY can override its tone. AGENTD_PERSONA=0.
    persona_enabled: bool = True  # AGENTD_PERSONA
    persona_file: str | None = (
        None  # path to SOUL.md; default set in load_config; AGENTD_PERSONA_FILE
    )
    # Long-term memory (Phase 3): when on, the agent gets remember/memory_search/memory_get
    # tools backed by a durable bank (<state_dir>/memory.sqlite) it can recall across
    # sessions. OFF by default (additive; AGENTD_MEMORY=1 to enable).
    memory_enabled: bool = False  # AGENTD_MEMORY
    # Semantic memory (RAG). When memory is on, notes are embedded on write and memory_search ranks
    # by cosine instead of keywords. The embedding model is a plugins knob: config
    # plugins.memory.tools.embed (defaults ON with a Gemini embed model). Provider-neutral via
    # litellm; point it at a local ollama model for no-key/no-cost embeddings.
    # Auto-recall: silently retrieve relevant memories and prepend them to the prompt on each
    # INTERACTIVE (user) turn — the agent doesn't call a tool. Needs an embedding model + memory.
    memory_auto_recall: bool = True  # AGENTD_MEMORY_AUTO_RECALL
    memory_auto_recall_limit: int = 5  # AGENTD_MEMORY_AUTO_RECALL_LIMIT
    memory_recall_min_score: float = (
        0.0  # cosine floor for a hit (0 = keep top-K); AGENTD_MEMORY_RECALL_MIN_SCORE
    )
    # Dreaming: a periodic consolidation pass (run it on a cron/heartbeat via memory_consolidate).
    # Merges near-duplicate notes, promotes durable short-term memories to long-term, and forgets
    # stale never-recalled ones. Thresholds mirror OpenClaw's deep-dreaming defaults.
    memory_dreaming_min_score: float = 0.8  # AGENTD_MEMORY_DREAMING_MIN_SCORE
    memory_dreaming_min_recall_count: int = 3  # AGENTD_MEMORY_DREAMING_MIN_RECALL_COUNT
    memory_dreaming_min_unique_queries: int = 3  # AGENTD_MEMORY_DREAMING_MIN_UNIQUE_QUERIES
    memory_dreaming_recency_half_life_days: float = 14.0  # AGENTD_MEMORY_DREAMING_HALF_LIFE_DAYS
    memory_dreaming_max_age_days: int = (
        30  # forget short-tier notes older than this that never stuck
    )
    memory_dreaming_merge_threshold: float = 0.92  # cosine >= => near-duplicate, keep the newer
    # Context compaction (Phase 3.5 / S7): cap the message history sent to the model to the
    # most-recent N (boundary-safe truncation). 0 = off (send everything). AGENTD_CONTEXT_MAX.
    context_max_messages: int = 0
    # Sub-agents (Phase 4a / S8): the agent can delegate a subtask to a fresh child run via
    # `spawn_subagent` and get its result back. OFF by default; AGENTD_SUBAGENTS=1 to enable.
    subagents_enabled: bool = False  # AGENTD_SUBAGENTS
    subagent_max: int = 4  # max concurrent child runs (runaway guard)
    # Nesting depth for sub-agent spawning: 1 = no nesting (orchestrator -> leaf children only),
    # up to 5. AGENTD_SUBAGENT_MAX_DEPTH. (A leaf at max depth cannot spawn further.)
    subagent_max_depth: int = 1  # AGENTD_SUBAGENT_MAX_DEPTH (1..5)
    # @mention routing (Layer B): what an explicit user @mention of ANOTHER agent does.
    #   "direct"   — that agent answers the turn AS ITSELF, in the same thread, one-off (no
    #                sub-agent hop); the next message reverts to the current agent.  [default]
    #   "delegate" — the current agent stays the driver and delegates via `message_agent` (the
    #                sub-agent block), weaving the reply into its own answer.
    # Only a single, unambiguous mention routes direct; two+ mentions always delegate (the
    # current agent orchestrates several specialists in one turn). AGENTD_MENTION_ROUTING.
    mention_routing: str = "direct"  # AGENTD_MENTION_ROUTING (direct | delegate)
    # skill_workshop (S10): the agent authors reusable SKILL.md playbooks at runtime.
    # OFF by default; AGENTD_SKILL_WORKSHOP=1 to enable.
    skill_workshop: bool = False  # AGENTD_SKILL_WORKSHOP
    # agent_workshop: author new agent DEFINITIONS (agents/<id>/ = agent.toml + IDENTITY.md)
    # by chatting; create_agent registers them LIVE (no restart). OFF by default.
    agent_workshop: bool = False  # AGENTD_AGENT_WORKSHOP
    # mcp_workshop: the agent can connect an MCP server by chatting (add_mcp) — config + connect,
    # loads its tools live. OFF by default; only add servers you trust.
    mcp_workshop: bool = False  # AGENTD_MCP_WORKSHOP
    # tool_workshop: the agent can author + hot-load a NEW native tool by chatting (create_tool).
    # This writes and RUNS new Python in-process (RCE by design) — OFF by default, opt-in by env.
    tool_workshop: bool = False  # AGENTD_TOOL_WORKSHOP
    # agent_messaging: the agent can message OTHER persistent agents and get a reply (message_agent),
    # honoring each agent's [subagents] allow scope. OFF by default.
    agent_messaging_enabled: bool = False  # AGENTD_AGENT_MESSAGING
    # sandbox_untrusted_plugins: route the UNTRUSTED tool tier (tools that ship inside a marketplace
    # agent's own package, agents/<id>/plugins/) through a PluginSandbox instead of running them
    # in-process. OFF by default; the default backend is an in-process passthrough (proves the seam,
    # no real isolation) until a container/microVM/remote backend is wired. AGENTD_SANDBOX_PLUGINS=1.
    sandbox_untrusted_plugins: bool = False  # AGENTD_SANDBOX_PLUGINS
    # sandbox_trusted_plugins: plugin ids to EXEMPT from the sandbox even when the above is on
    # (local dev convenience for a plugin you author yourself). Never trust a plugin you didn't write.
    sandbox_trusted_plugins: tuple = ()
    # Model failover (S11): models to try, in order, when the primary errors before any
    # output. Empty = no failover. AGENTD_MODEL_FALLBACKS=comma,separated,ids.
    model_fallbacks: list = field(default_factory=list)
    # Cost-efficiency brain ROUTING (decoupled seam; default OFF => unchanged: one brain does
    # everything). When enabled, the loop picks the brain model per iteration by NEED — a cheap
    # `text_model` for ordinary turns, escalating to `vision_model` ONLY on iterations whose context
    # actually carries an image the brain must SEE (an ImageContent block). Generating an image needs
    # NO escalation — that's a tool call the text brain makes with text args; only READING image
    # pixels does. An unset side falls back to the agent's normal brain. CONFIG-ONLY (models live in
    # config, not env), resolved by infrastructure/llm/model_router.build_model_router. Shape:
    #   "cost_efficiency": {"enabled": true,
    #                       "text_model":   "deepseek/deepseek-v4-pro",        # cheap default brain
    #                       "vision_model": "gemini/gemini-3.1-pro-preview"}   # omit => keep normal brain
    # OFF (or both models unset) => no router => the agent's normal brain runs every turn, unchanged.
    cost_efficiency: dict = field(default_factory=dict)
    # PLATFORM MODEL PROXY (platform-keys mode). Default OFF => every model call goes DIRECT to
    # the provider with the local/BYOK key, unchanged. When on, ALL model calls route through OUR
    # LiteLLM proxy (which holds our provider keys + meters per account). Shape:
    #   "model_proxy": {"enabled": true, "api_base": "http://localhost:4000"}
    # The proxy KEY is a SECRET from the env (AGENTD_MODEL_PROXY_KEY), never here. The URL may
    # also come from AGENTD_MODEL_PROXY_URL (env wins). Resolved by
    # infrastructure/llm/model_proxy.configure at boot.
    model_proxy: dict = field(default_factory=dict)
    # Deprecated read-only compatibility input. New writes use model_proxy.
    model_gateway: dict = field(default_factory=dict)
    # PLATFORM ACCOUNTS (hosted identity + per-account metering). Default OFF => the daemon has no
    # notion of accounts; connections authenticate with the single machine token and no spend is
    # metered per user. When on, the connection gate resolves each client's session token to an
    # account (State plane) and the run meters that account's model spend. Shape:
    #   "accounts": {"enabled": true, "api_base": "http://localhost:4100"}
    # The URL may also come from AGENTD_ACCOUNTS_URL (env wins). No secret here — the session token
    # is the client's credential. Resolved by infrastructure/llm/accounts.configure at boot.
    accounts: dict = field(default_factory=dict)
    # OBSERVABILITY (display-only): emit a per-step `model_trace` event — which brain model ran each
    # loop iteration + its token usage — so a client can show "step 1 deepseek 1.2k/0.5k → step 2
    # gemini …". Default ON; AGENTD_MODEL_TRACE=0 turns it off. No effect on the run itself.
    model_trace: bool = True
    # Execution sandbox (S17, seam): "" / "local" = run on host (default, unchanged);
    # "docker"/"ssh" select an isolating adapter (not yet implemented). AGENTD_SANDBOX.
    sandbox: str = ""

    # --- autonomy (heartbeat) — Phase 2 ----------------------------------------
    # OFF by default. When on, the shared scheduler wakes each agent that declares a
    # `heartbeat` interval (in its agent.toml) to read its HEARTBEAT.md and act on a
    # tick. The reactive chat path is unchanged either way. AGENTD_AUTONOMY=1 to enable.
    autonomy_enabled: bool = False
    heartbeat_default_interval: str = ""  # e.g. "30m"; per-agent agent.toml overrides
    heartbeat_active_hours: str = ""  # e.g. "08:00-22:00" (empty = always)
    # Outbound notifications (Phase 5a): the gateway reaches the user when a scheduled
    # run ends blocked/failed (client-push + durable). On by default; AGENTD_NOTIFY=0
    # disables. Only ever fires under autonomy (no cron runs => nothing to notify).
    notify_enabled: bool = True
    # RUN seam (reliability): a scheduled run that finishes without the agent declaring an
    # outcome (report_outcome) is recorded as `incomplete`, not a silent `ok` — so a run
    # can't claim success it never verified. AGENTD_ENFORCE_OUTCOME=0 to cut this layer.
    enforce_outcome: bool = True
    # A scheduled job auto-PAUSES (and notifies once) after this many consecutive
    # failed/incomplete runs, so a chronically-broken job stops re-running + spamming.
    # A job's own failure_alert (agent.toml) overrides this. AGENTD_CRON_FAILURE_ALERT=0 to disable.
    cron_failure_alert_default: int = 3
    # Default Google account for the workspace MCP — set ONCE here and every agent uses it;
    # an agent's own `google_account` in agent.toml overrides it. AGENTD_GOOGLE_ACCOUNT.
    google_account: str = ""
    # TURN seam (workspace awareness): inject a manifest of the agent's workspace files
    # (scripts/docs/images/data) into every prompt so resources it created are always
    # visible and reusable. AGENTD_WORKSPACE_INDEX=0 to cut this layer.
    workspace_index_enabled: bool = True
    workspace_index_max_files: int = 100  # cap the manifest (AGENTD_WORKSPACE_INDEX_MAX)
    # Resource Manager (OpenClaw-style): a cached, DESCRIBED index of workspace resources
    # (scripts/docs/images/data) + a `resource` CRUD tool. Supersedes the plain workspace
    # index for the manifest when on. AGENTD_RESOURCES=0 to cut this layer.
    resource_manager_enabled: bool = True
    resource_index_max_files: int = 100  # cap the index (AGENTD_RESOURCES_MAX)
    # Optional Gemini VISION captions for image resources, computed in the manager's
    # BACKGROUND (never on the agent's path). OFF by default: it sends image bytes to
    # Google + costs API calls. AGENTD_RESOURCE_VISION=1 to enable.
    resource_vision_enabled: bool = False  # caption images  (AGENTD_RESOURCE_VISION)
    # LLM one-line SUMMARIES for text resources (scripts/docs/data + extracted docx/pdf/xlsx),
    # computed in the BACKGROUND. OFF by default: it sends file content to Google + costs API
    # calls. AGENTD_RESOURCE_SUMMARIZE=1. Both tiers reuse the same model/timeout below.
    resource_summarize_enabled: bool = False  # summarize text  (AGENTD_RESOURCE_SUMMARIZE)
    # Both model tiers are plugins knobs: image captions (google-genai, Gemini only) resolve from
    # config plugins.resources.tools.caption (which also carries the caption timeout knob
    # `timeout_seconds`, default 60.0, read via tool_config); text summaries (litellm, any provider)
    # from plugins.resources.tools.summarize -> verify chain -> brain.
    # Messaging channels (Phase 5b): JSON list of channel configs (default none => off).
    # Each: {type: email|memory|line, agent, policy?, allow_from?, webhook_path?,
    # notify_to?, ...}. A poll channel polls for inbound -> runs its agent -> replies on
    # the same channel; a PUSH channel (line) instead receives via the webhook server
    # below. One with `notify_to` also delivers notifications there (one transport reused).
    channels: list = field(default_factory=list)
    channel_poll_seconds: float = 15.0  # inbound poll cadence (AGENTD_CHANNEL_POLL)
    # Webhook ingress for PUSH channels (LINE etc.). Started only when a channel exposes a
    # webhook_path. Bind loopback + a tunnel/relay in front for reachability.
    webhook_host: str = "0.0.0.0"  # AGENTD_WEBHOOK_HOST
    webhook_port: int = 8788  # AGENTD_WEBHOOK_PORT
    # Public base URL the gateway is reachable at (your ngrok/tunnel/domain), used to build
    # tappable links — e.g. the /connect login-setup form. Blank = no auto links. AGENTD_PUBLIC_URL.
    public_url: str = ""  # e.g. https://6dda-….ngrok-free.app
    # Webhook TASK triggers (D): external events (git push / CI / any service) POST to
    # /hook/<id> to RUN an agent — distinct from conversational channels. Each hook:
    # {id, secret, agent, allow?: [ids], task?: default-task}. Served on the SAME webhook
    # server. webhook_workshop lets the agent mint hooks by chatting (create_webhook).
    webhooks: list = field(default_factory=list)
    webhook_workshop: bool = False  # AGENTD_WEBHOOK_WORKSHOP

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
    # The computer-use MODEL + all its behavioral KNOBS live in the plugins block, config-only,
    # tool-level (plugins.computer.tools.computer.<knob>) — read via tool_config (tool_models.py:
    # computer_knob). model (Gemini's dedicated computer-use model, via google-genai); knobs +
    # built-in defaults: max_steps (25; loop step cap), send_max (1440; longest screenshot side sent,
    # Gemini docs ~1440x900), capture ("primary" | "virtual" multi-monitor), pause (0.15; pyautogui
    # inter-action settle secs), call_timeout_seconds (120.0; per model call), save_screenshots (False;
    # DEV ONLY — persists each step's screenshot to state_dir/screenshots/, privacy + disk cost),
    # corral_to_primary (True; Windows-only, pull off-monitor windows onto the captured screen).
    # CONFIG-ONLY (no AGENTD_COMPUTER_* env for these); per-agent-overridable via agent.toml.


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
        value = value[: min(cuts)]
    return value.rstrip()


def _load_dotenv() -> None:
    """Load KEY=VALUE lines from agent_runtime's own .env files into os.environ (no override).

    Repo mode: v2/.env (anchored — never a .env outside the v2 folder), then the
    user's ~/.agentd/.env. Packaged mode: only the user file. First definition wins.
    """
    for env_path in runtime_paths.env_files():
        if not env_path.is_file():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), _dotenv_value(value)
            if key and value and key not in os.environ:
                os.environ[key] = value


def default_local_registry(state_dir) -> str:
    """The zero-config marketplace source: ``<state_dir>/registry`` — but only when a
    built ``index.json`` actually sits there (an empty/missing dir means 'not set up',
    not 'a broken registry'). Lowest-priority fallback in the resolution chain."""
    registry_dir = Path(state_dir) / "registry"
    return str(registry_dir) if (registry_dir / "index.json").is_file() else ""


def load_config(path: Path | None = None) -> Config:
    _load_dotenv()
    cfg = Config()

    candidates = [path] if path else runtime_paths.config_candidates()
    for candidate in candidates:
        if candidate and candidate.is_file():
            data = json.loads(candidate.read_text(encoding="utf-8"))
            for key, value in data.items():
                # An install's identity + secrets never come from the user JSON:
                # `distribution` is the installer's file, `gateway_token` is env-only.
                if key in ("distribution", "gateway_token", "config_path"):
                    continue
                if hasattr(cfg, key):
                    if key in ("workspace", "state_dir", "skills_dir", "agents_dir"):
                        value = Path(value).expanduser()
                    setattr(cfg, key, value)
            cfg.config_path = str(candidate)
            break
    if not cfg.config_path:
        logging.getLogger("agentd").warning(
            "CONFIG MISSING: no agentd.config.json found (searched: %s). Models/knobs fall back to "
            "built-in defaults; the models layer will refuse to resolve until a config exists.",
            ", ".join(str(c) for c in candidates if c),
        )

    if os.environ.get("AGENTD_AGENT_NAME"):
        cfg.agent_name = os.environ["AGENTD_AGENT_NAME"]
    # NOTE: the brain `model` is CONFIG-ONLY (read via tool_models.brain_model). AGENTD_MODEL is
    # deliberately NOT consulted — models live in config, not env (env = keys, config = knobs).
    # NOTE: every model-bearing TOOL/SUBSYSTEM model AND every plugin backend PROVIDER now lives in the
    # plugins map, which is CONFIG-ONLY (a knob, not a secret) — loaded from agent_runtime.config.json's
    # "plugins" key with NO env override by design (env holds keys, not knobs). So there are deliberately
    # no AGENTD_*_MODEL / IMAGEGEN_PROVIDER env vars anymore for search/verify/safe_to_send/resource/
    # computer/memory-embed/skills-relevance/figure-art.
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
    # NOTE: browser behavioral knobs (headless/persistent/cdp_url/downloads/channel/stealth/
    # cursor_scan/chrome_profile/action_timeout_ms/agent_browser_command) are CONFIG-ONLY now —
    # plugins.browser.tools.browser.<knob>, read via tool_config. No AGENTD_BROWSER_* env for them.
    if os.environ.get("BRAVE_API_KEY"):
        cfg.brave_api_key = os.environ["BRAVE_API_KEY"]
    if os.environ.get("AGENTD_AUTONOMY") is not None:
        cfg.autonomy_enabled = os.environ["AGENTD_AUTONOMY"].lower() in ("1", "true", "yes", "on")
    if os.environ.get("AGENTD_NOTIFY"):
        cfg.notify_enabled = os.environ["AGENTD_NOTIFY"].lower() in ("1", "true", "yes", "on")
    if os.environ.get("AGENTD_ENFORCE_OUTCOME"):
        cfg.enforce_outcome = os.environ["AGENTD_ENFORCE_OUTCOME"].lower() not in (
            "0",
            "false",
            "no",
            "",
        )
    if os.environ.get("AGENTD_CRON_FAILURE_ALERT"):
        cfg.cron_failure_alert_default = int(os.environ["AGENTD_CRON_FAILURE_ALERT"])
    if os.environ.get("AGENTD_GOOGLE_ACCOUNT"):
        cfg.google_account = os.environ["AGENTD_GOOGLE_ACCOUNT"].strip()
    if os.environ.get("AGENTD_WORKSPACE_INDEX"):
        cfg.workspace_index_enabled = os.environ["AGENTD_WORKSPACE_INDEX"].lower() not in (
            "0",
            "false",
            "no",
            "",
        )
    if os.environ.get("AGENTD_WORKSPACE_INDEX_MAX"):
        cfg.workspace_index_max_files = int(os.environ["AGENTD_WORKSPACE_INDEX_MAX"])
    if os.environ.get("AGENTD_SCRATCH_TTL_HOURS"):
        cfg.scratch_ttl_hours = float(os.environ["AGENTD_SCRATCH_TTL_HOURS"])
    if os.environ.get("AGENTD_EVENT_LOG"):
        cfg.event_log_enabled = os.environ["AGENTD_EVENT_LOG"].lower() not in (
            "0",
            "false",
            "no",
            "",
        )
    if os.environ.get("AGENTD_MODEL_TRACE"):
        cfg.model_trace = os.environ["AGENTD_MODEL_TRACE"].lower() not in ("0", "false", "no", "")
    if os.environ.get("AGENTD_EVENT_LOG_MAX"):
        cfg.event_log_max_runs = int(os.environ["AGENTD_EVENT_LOG_MAX"])
    if os.environ.get("AGENTD_RESOURCES"):
        cfg.resource_manager_enabled = os.environ["AGENTD_RESOURCES"].lower() not in (
            "0",
            "false",
            "no",
            "",
        )
    if os.environ.get("AGENTD_RESOURCES_MAX"):
        cfg.resource_index_max_files = int(os.environ["AGENTD_RESOURCES_MAX"])
    if os.environ.get("AGENTD_RESOURCE_VISION"):
        cfg.resource_vision_enabled = os.environ["AGENTD_RESOURCE_VISION"].lower() not in (
            "0",
            "false",
            "no",
            "",
        )
    if os.environ.get("AGENTD_RESOURCE_SUMMARIZE"):
        cfg.resource_summarize_enabled = os.environ["AGENTD_RESOURCE_SUMMARIZE"].lower() not in (
            "0",
            "false",
            "no",
            "",
        )
    if os.environ.get("AGENTD_CHANNEL_POLL"):
        cfg.channel_poll_seconds = float(os.environ["AGENTD_CHANNEL_POLL"])
    if os.environ.get("AGENTD_WEBHOOK_HOST"):
        cfg.webhook_host = os.environ["AGENTD_WEBHOOK_HOST"]
    if os.environ.get("AGENTD_WEBHOOK_PORT"):
        cfg.webhook_port = int(os.environ["AGENTD_WEBHOOK_PORT"])
    if os.environ.get("AGENTD_PUBLIC_URL"):
        cfg.public_url = os.environ["AGENTD_PUBLIC_URL"].strip()
    if os.environ.get("AGENTD_WEBHOOK_WORKSHOP"):
        cfg.webhook_workshop = os.environ["AGENTD_WEBHOOK_WORKSHOP"].lower() not in (
            "0",
            "false",
            "no",
            "",
        )
    if os.environ.get("AGENTD_HEARTBEAT_INTERVAL"):
        cfg.heartbeat_default_interval = os.environ["AGENTD_HEARTBEAT_INTERVAL"]
    if os.environ.get("AGENTD_HEARTBEAT_HOURS"):
        cfg.heartbeat_active_hours = os.environ["AGENTD_HEARTBEAT_HOURS"]
    if os.environ.get("AGENTD_PARALLEL_SEARCH") is not None:
        cfg.parallel_search_enabled = os.environ["AGENTD_PARALLEL_SEARCH"].lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
    if os.environ.get("PARALLEL_API_KEY"):
        cfg.parallel_api_key = os.environ["PARALLEL_API_KEY"]
    if os.environ.get("AGENTD_COMPUTER_ENABLED"):
        cfg.computer_enabled = os.environ["AGENTD_COMPUTER_ENABLED"].lower() not in (
            "0",
            "false",
            "no",
            "",
        )
    # NOTE: computer behavioral knobs (max_steps/send_max/capture/pause/call_timeout_seconds/
    # save_screenshots/corral_to_primary) + its model are CONFIG-ONLY — plugins.computer.tools.computer.
    # <knob>, read via tool_config. Only the enable flag above stays an env toggle (it gates registration).
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
    if os.environ.get("AGENTD_COMPLETENESS_CHECK"):
        cfg.completeness_check = os.environ["AGENTD_COMPLETENESS_CHECK"].lower() not in (
            "0",
            "false",
            "no",
            "",
        )
    if os.environ.get("AGENTD_EXECUTION_CONTRACT"):
        cfg.execution_contract = os.environ["AGENTD_EXECUTION_CONTRACT"].strip()
    # tool-catalog enablement (global on/off) + plugins dir
    if os.environ.get("AGENTD_TOOLS_DISABLED"):
        cfg.tools_disabled = [
            s.strip() for s in os.environ["AGENTD_TOOLS_DISABLED"].split(",") if s.strip()
        ]
    if os.environ.get("AGENTD_TOOLS_ENABLED"):
        cfg.tools_enabled = [
            s.strip() for s in os.environ["AGENTD_TOOLS_ENABLED"].split(",") if s.strip()
        ]
    if os.environ.get("AGENTD_PLUGINS_DIR"):
        cfg.plugins_dir = os.environ["AGENTD_PLUGINS_DIR"].strip()
    if not cfg.plugins_dir:  # default drop-in folder: repo plugins/ | ~/.agentd/plugins
        cfg.plugins_dir = str(runtime_paths.default_user_plugins_dir())
    # the SHIPPED built-ins always load from their own root (repo plugins/ in a checkout,
    # agent_runtime/_builtin_plugins in a wheel), even if plugins_dir is overridden.
    cfg.builtin_plugins_dir = str(runtime_paths.builtin_plugins_dir())
    if os.environ.get("AGENTD_SKILLS_PROMPT_MAX"):
        cfg.skills_prompt_max = int(os.environ["AGENTD_SKILLS_PROMPT_MAX"])
    if os.environ.get("AGENTD_SKILLS_PROMPT_CHARS"):
        cfg.skills_prompt_chars = int(os.environ["AGENTD_SKILLS_PROMPT_CHARS"])
    if os.environ.get("AGENTD_SKILLS_RELEVANCE_ENABLED"):
        cfg.skills_relevance_enabled = os.environ["AGENTD_SKILLS_RELEVANCE_ENABLED"].lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
    if os.environ.get("AGENTD_SKILLS_RELEVANCE_TOP_K"):
        cfg.skills_relevance_top_k = int(os.environ["AGENTD_SKILLS_RELEVANCE_TOP_K"])
    if os.environ.get("AGENTD_SAFE_TO_SEND"):
        cfg.safe_to_send_check = os.environ["AGENTD_SAFE_TO_SEND"].lower() not in (
            "0",
            "false",
            "no",
            "",
        )
    if os.environ.get("AGENTD_PERSONA"):
        cfg.persona_enabled = os.environ["AGENTD_PERSONA"].lower() not in ("0", "false", "no", "")
    cfg.persona_file = (
        os.environ.get("AGENTD_PERSONA_FILE")
        or cfg.persona_file
        or str(Path(cfg.state_dir).parent / "SOUL.md")
    )  # default: the repo-root SOUL.md (editable)
    if os.environ.get("AGENTD_MEMORY"):
        cfg.memory_enabled = os.environ["AGENTD_MEMORY"].lower() not in ("0", "false", "no", "")
    if os.environ.get("AGENTD_MEMORY_AUTO_RECALL"):
        cfg.memory_auto_recall = os.environ["AGENTD_MEMORY_AUTO_RECALL"].lower() not in (
            "0",
            "false",
            "no",
            "",
        )
    if os.environ.get("AGENTD_MEMORY_AUTO_RECALL_LIMIT"):
        cfg.memory_auto_recall_limit = int(os.environ["AGENTD_MEMORY_AUTO_RECALL_LIMIT"])
    if os.environ.get("AGENTD_MEMORY_RECALL_MIN_SCORE"):
        cfg.memory_recall_min_score = float(os.environ["AGENTD_MEMORY_RECALL_MIN_SCORE"])
    if os.environ.get("AGENTD_CONTEXT_MAX"):
        cfg.context_max_messages = int(os.environ["AGENTD_CONTEXT_MAX"])
    if os.environ.get("AGENTD_SUBAGENTS"):
        cfg.subagents_enabled = os.environ["AGENTD_SUBAGENTS"].lower() not in (
            "0",
            "false",
            "no",
            "",
        )
    if os.environ.get("AGENTD_SUBAGENT_MAX_DEPTH"):
        cfg.subagent_max_depth = int(os.environ["AGENTD_SUBAGENT_MAX_DEPTH"])
    if os.environ.get("AGENTD_MENTION_ROUTING"):
        cfg.mention_routing = os.environ["AGENTD_MENTION_ROUTING"].strip().lower()
    if os.environ.get("AGENTD_SKILL_WORKSHOP"):
        cfg.skill_workshop = os.environ["AGENTD_SKILL_WORKSHOP"].lower() not in (
            "0",
            "false",
            "no",
            "",
        )
    if os.environ.get("AGENTD_AGENT_WORKSHOP"):
        cfg.agent_workshop = os.environ["AGENTD_AGENT_WORKSHOP"].lower() not in (
            "0",
            "false",
            "no",
            "",
        )
    if os.environ.get("AGENTD_MCP_WORKSHOP"):
        cfg.mcp_workshop = os.environ["AGENTD_MCP_WORKSHOP"].lower() not in ("0", "false", "no", "")
    if os.environ.get("AGENTD_TOOL_WORKSHOP"):
        cfg.tool_workshop = os.environ["AGENTD_TOOL_WORKSHOP"].lower() not in (
            "0",
            "false",
            "no",
            "",
        )
    if os.environ.get("AGENTD_AGENT_MESSAGING"):
        cfg.agent_messaging_enabled = os.environ["AGENTD_AGENT_MESSAGING"].lower() not in (
            "0",
            "false",
            "no",
            "",
        )
    if os.environ.get("AGENTD_SANDBOX_PLUGINS"):
        cfg.sandbox_untrusted_plugins = os.environ["AGENTD_SANDBOX_PLUGINS"].lower() not in (
            "0",
            "false",
            "no",
            "",
        )
    if os.environ.get("AGENTD_MODEL_FALLBACKS"):
        cfg.model_fallbacks = [
            s.strip() for s in os.environ["AGENTD_MODEL_FALLBACKS"].split(",") if s.strip()
        ]

    # --- distribution profile + marketplace + gateway auth (M2/M4/M6) -------------
    # The profile decides what THIS INSTALL is; it never comes from the user JSON.
    cfg.distribution = load_profile()
    if cfg.distribution.default_agent and cfg.agent_id == "main":
        cfg.agent_id = cfg.distribution.default_agent
    # Acquired addons JOIN the provisioning set (tiers doc §3): when the profile gates
    # plugins, union in every plugin an installed bundle placed — a store install on a
    # Studio flavor stays provisioned across restarts. (The ledger is state, not config,
    # so it is read fresh on every load.)
    if cfg.distribution.provisioned_plugins is not None:
        try:
            ledger = json.loads(
                (Path(cfg.state_dir) / "installed_bundles.json").read_text(encoding="utf-8")
            )
            bundle_plugins = tuple(
                str(p) for b in ledger.get("bundles", []) for p in (b.get("plugin_ids") or [])
            )
            if bundle_plugins:
                from dataclasses import replace

                cfg.distribution = replace(
                    cfg.distribution,
                    provisioned_plugins=tuple(
                        dict.fromkeys(cfg.distribution.provisioned_plugins + bundle_plugins)
                    ),
                )
        except (OSError, ValueError):
            pass  # no ledger / unreadable => nothing installed yet
    # registry resolution: env > JSON config (already applied above) > profile >
    # a LOCAL registry at <state_dir>/registry (local-first: drop .agentpkg files
    # there + `agentd bundle index` and the store just works, no cloud needed).
    if os.environ.get("AGENTD_REGISTRY"):
        cfg.registry_url = os.environ["AGENTD_REGISTRY"].strip()
    elif not cfg.registry_url:
        cfg.registry_url = cfg.distribution.registry_url or default_local_registry(cfg.state_dir)
    if os.environ.get("AGENTD_GATEWAY_AUTH"):
        cfg.gateway_auth = os.environ["AGENTD_GATEWAY_AUTH"].lower() not in ("0", "false", "no", "")
    if os.environ.get("AGENTD_TOKEN"):
        cfg.gateway_token = os.environ["AGENTD_TOKEN"].strip()
    if os.environ.get("AGENTD_APP_HOSTS"):
        # JSON object string: {"weather.example.com": "weather"}. A typo must not kill
        # the daemon — warn and keep the config value instead.
        try:
            parsed = json.loads(os.environ["AGENTD_APP_HOSTS"])
            if isinstance(parsed, dict):
                cfg.app_hosts = {
                    str(k).strip().lower(): str(v).strip() for k, v in parsed.items()
                }
            else:
                logging.getLogger("agentd").warning("AGENTD_APP_HOSTS ignored: not a JSON object")
        except (ValueError, TypeError):
            logging.getLogger("agentd").warning("AGENTD_APP_HOSTS ignored: invalid JSON")

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
    """Decide the browser engine from the plugins config: `plugins.browser.provider`.

    Returns "playwright" (our built-in engine, the DEFAULT) or "agent_browser" (external, via its MCP
    server). Anything other than "agent_browser" => "playwright", so the browser is never accidentally
    lost (and per-agent overridable via agent.toml [plugins.browser])."""
    from agent_runtime.application.tool_models import resolve_tool_provider

    prov = resolve_tool_provider(config, "browser", "browser", default="playwright")
    return "agent_browser" if prov == "agent_browser" else "playwright"

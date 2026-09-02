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
    # The WILDCARD companion to app_hosts: a base domain ("example.com") under which every
    # subdomain names the agent whose id is the label — weather.example.com serves agent
    # "weather" with NO per-agent configuration, which is what lets "publish" mean "gets a
    # URL". app_hosts still wins for a hostname it names exactly (so platform.example.com
    # can point at cloud-agent-builder, whose id is not "platform"), and RESERVED_HOST_LABELS
    # never derive (www/api/admin/... belong to the product, not to whoever publishes first —
    # the same set the publish service refuses to let anyone claim as a bundle id). Empty
    # (the default, every local install) => fully dormant. Override AGENTD_APP_HOST_SUFFIX.
    app_host_suffix: str = ""

    # --- distribution (what THIS INSTALL is) + marketplace ------------------------
    # The parsed distribution.toml (product name/flavor, provisioned plugin set, store
    # wiring) — the OPEN profile when no file exists. Loaded by load_config; NOT settable
    # from JSON (an install's identity comes from the installer, not the user config).
    distribution: DistributionProfile = field(default_factory=lambda: DistributionProfile())
    # The bundle registry (marketplace index.json): file:// path, https URL, or a bare
    # local directory. Resolution: AGENTD_REGISTRY > JSON config > distribution profile.
    registry_url: str = ""

    # --- PUBLISHING (the write side of the registry above) ----------------------
    # Where `publish_agent` / `agentd bundle publish` send a built bundle: an s3:// target
    # (s3://bucket[/prefix]) or a plain local directory. EMPTY BY DEFAULT, and that default is
    # load-bearing: an install with no target cannot publish anywhere, so a downloaded copy of
    # this product can never push to someone else's marketplace just because the tool exists.
    # AGENTD_PUBLISH_TARGET overrides (same name the deploy script already reads).
    publish_target: str = ""
    # PATH to the ed25519 keypair from `agentd bundle keygen` — never the key itself, and never
    # logged. Signing is what makes a bundle verifiable against the publisher_key that installed
    # clients pin; without it a publish is refused rather than silently unsigned.
    # AGENTD_PUBLISHER_KEYFILE overrides. (Distinct from AGENTD_PUBLISHER_KEY, which is the
    # PUBLIC half used to verify downloads — see the note further down.)
    publisher_keyfile: str = ""

    # --- PRODUCTS (one agent, shipped as its own app: `agentd product build`) ----
    # A per-agent installer is a small STUB that ensures the shared ENGINE is present, writes a
    # ~50 KB payload, and makes a shortcut. So a stub has to know which engine to fetch and how to
    # verify it. All four are EMPTY BY DEFAULT and there is no baked fallback anywhere: a default
    # url would produce installers that download from a host this build has never heard of and
    # fail only on a stranger's machine.
    #
    # Normally these stay empty and the engine is read from the registry index's `engine` block
    # (published once per engine release, so stubs follow it without being rebuilt). Set them to
    # override that — e.g. to test an engine that is not published yet.
    # AGENTD_ENGINE_URL / AGENTD_ENGINE_SHA256 / AGENTD_ENGINE_VERSION override.
    engine_installer_url: str = ""
    engine_installer_sha256: str = ""  # a stub REFUSES to run a download it cannot verify
    engine_installer_platform: str = "win"  # which platform the two values above describe
    engine_version: str = ""  # what that installer installs, for the min-version check
    # The lowest engine that can run payloads built by THIS install. Empty means "any", which is
    # correct because the engine<->payload contract is additive-only. Set it only when a payload
    # genuinely needs something a specific engine introduced — it makes a stub update the engine
    # instead of opening a window that half works.
    engine_min_version: str = ""
    # Explicit path to makensis; "" => discovered on PATH. Set it when NSIS is installed somewhere
    # unusual, never as a way to bake in one machine's layout.
    makensis_path: str = ""

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
    # HOW LONG A RUN MAY GO SILENT. The two above guard the streaming call and nothing else, so
    # a run that wedges anywhere outside it — between a tool result and the next request, in a
    # tool that never returns — was never going to end, and the window that started it stayed
    # locked on "running" forever with a Stop button that had nothing to stop.
    #
    # SILENCE, NOT WALL TIME. This was briefly a ceiling on the whole run, and that was wrong: a
    # complex build that is genuinely working — streaming, calling tools, making progress — would
    # be killed for taking too long, which is the one thing a timeout must never do. What actually
    # distinguishes a wedged run is that NOTHING HAPPENS, so that is what is measured. Any event
    # resets it, so a run hammering away for three hours never trips it.
    #
    # Generous by default: a slow tool can legitimately be quiet for minutes, and the cost of
    # waiting a little longer is far lower than the cost of cutting off real work.
    run_idle_timeout_seconds: float = (
        600.0  # 10 min of SILENCE ends a run (AGENTD_RUN_IDLE_TIMEOUT)
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
    # (agent_workshop / tool_workshop were removed: create_agent and create_tool are no longer
    # shared tools needing a process-wide switch. They are PRIVATE to the agent-builder agent
    # — agents/agent-builder/plugins/agent-authoring/ — so only that agent can reach them, and
    # a fresh install has a working Agent Builder without editing config.)
    # mcp_workshop: the agent can connect an MCP server by chatting (add_mcp) — config + connect,
    # loads its tools live. OFF by default; only add servers you trust.
    mcp_workshop: bool = False  # AGENTD_MCP_WORKSHOP
    # agent_messaging: the agent can message OTHER persistent agents and get a reply (message_agent),
    # honoring each agent's [subagents] allow scope. OFF by default.
    agent_messaging_enabled: bool = False  # AGENTD_AGENT_MESSAGING
    # sandbox_untrusted_plugins: route the UNTRUSTED tool tier (tools that ship inside a marketplace
    # agent's own package, agents/<id>/plugins/) through a PluginSandbox instead of running them
    # in-process. ON BY DEFAULT, EVERYWHERE — desktop and hosted — because the trust boundary is
    # PROVENANCE, not deployment shape (see classify_origin: our own runtime plugins in v2/plugins
    # and agents authored on this machine stay first-party and in-process; only code the installer
    # laid down from a .agentpkg is wrapped). Default ON at the source so even a Config built without
    # load_config isolates downloaded code; disable with AGENTD_SANDBOX_PLUGINS=0.
    sandbox_untrusted_plugins: bool = True  # AGENTD_SANDBOX_PLUGINS=0 to disable
    # sandbox_trusted_plugins: plugin ids to EXEMPT from the sandbox even when the above is on
    # (local dev convenience for a plugin you author yourself). Never trust a plugin you didn't write.
    sandbox_trusted_plugins: tuple = ()
    # run_mode: which keys pay for model calls — "local" (BYOK, your own keys) or "cloud" (platform
    # keys, metered). PERSISTED like every other setting (config.set writes it), so the answer is the
    # SAME in every window instead of a per-window localStorage guess. Empty = not chosen yet, which
    # resolves to LOCAL (honest and safe: your own keys, no surprise billing). A HOSTED daemon ignores
    # this and forces cloud — BYOK is refused there (no per-account key store), so local cannot run.
    # AGENTD_RUN_MODE overrides.
    run_mode: str = ""
    # WHICH sandbox backend: "local" (in-process passthrough, no isolation) or "subprocess" (a child
    # process per tool call, scrubbed env, no runtime handles, audit-hook enforcement). Empty = the
    # HOST'S CAPABILITY decides: subprocess wherever a child process can be launched (always on
    # POSIX; on Windows when the daemon runs on the Proactor loop, which main.py ensures), else a
    # warned fallback to local. Not the deployment shape — third-party code is isolated on the
    # desktop exactly as in the cloud. AGENTD_SANDBOX_BACKEND overrides by name.
    sandbox_plugin_backend: str = ""
    # The interpreter the subprocess backend spawns. Empty = the one running the daemon (correct
    # almost always; an embedded runtime that relocates its python.exe is the exception).
    sandbox_python: str = ""
    # Environment variables an untrusted child INHERITS. Empty = the seed in the backend (PATH and
    # the handful the interpreter needs to boot). An ALLOWLIST on purpose: with a denylist, every
    # provider key added anywhere in future leaks into every sandbox until someone updates it.
    sandbox_env_passthrough: tuple = ()
    # Config fields the child may READ (it gets a projection, never the live Config — which holds
    # every provider key). Empty = the seed in sandbox/protocol.py. Anything not listed raises
    # AttributeError in the child, so getattr(config, x, default) returns the caller's default.
    sandbox_config_fields: tuple = ()
    # Let a sandboxed plugin load native code (ctypes). OFF: ctypes is the cheap way out of an
    # interpreter-level control, and a plugin that genuinely needs FFI is the one you least want
    # running next to other accounts' files.
    sandbox_allow_native: bool = False
    # POSIX + daemon-running-as-root ONLY: drop each sandbox child to this uid/gid before exec.
    # This is the one control here the KERNEL enforces rather than the interpreter — but it needs
    # the tenant workspace to be writable by that user, so it stays off until an operator sets it.
    sandbox_child_uid: int = 0
    sandbox_child_gid: int = 0
    # Ceilings for ONE untrusted tool call: {"timeout_s": 120, "cpu_ms": 0, "mem_mb": 0} (0 = no
    # limit). Wall clock is enforced everywhere; cpu/mem are POSIX rlimits, so they do nothing on
    # Windows — a memory bomb in a plugin is survivable on the hosted task and is not on a desktop.
    # NOTE the wall clock counts the TOOL's own time: it pauses while the host is serving a model
    # call for it, so a slow provider cannot kill an otherwise healthy tool.
    sandbox_limits: dict = field(default_factory=dict)
    # A sandboxed tool has no network and no keys, so it cannot call a model itself — it asks the
    # HOST to, and the host checks, clamps and meters the call against the account running the
    # agent. Ceilings per tool invocation: {"max_calls": 8, "max_output_tokens": 4096,
    # "timeout_s": 120}. Cost is the realistic failure mode here — a plugin stuck in a retry loop
    # spends real money — so these are finite by default rather than opt-in.
    sandbox_model_limits: dict = field(default_factory=dict)
    # Model ids a sandboxed tool may ask for. Empty = DERIVED per tool from the normal resolution
    # chain (what that tool would have used anyway), which is what you want; set it to pin the
    # whole deployment to an explicit list. Only tools declaring `needs_model` get any at all.
    sandbox_models: tuple = ()
    # OUTBOUND NETWORK for sandboxed plugins. The plugin declares the hosts it calls in its own
    # plugin.toml ([sandbox] net); these NARROW that declaration and can never widen it — an
    # operator allowlist that added hosts would grant reach the installed package never disclosed.
    #   sandbox_net_allow  non-empty => a declared host must ALSO match one of these
    #   sandbox_net_deny   removes matching hosts; a bare "*" switches outbound off entirely
    # Both accept exact hosts or a leading "*." subdomain wildcard.
    # AGENTD_SANDBOX_NET_ALLOW / AGENTD_SANDBOX_NET_DENY (comma-separated).
    sandbox_net_allow: tuple = ()
    sandbox_net_deny: tuple = ()
    # Ceilings for ONE tool run's host-brokered fetches; same shape and spirit as
    # sandbox_model_limits. Keys: max_calls, max_bytes, timeout_s. Defaults in
    # infrastructure/tools/sandbox/fetch_broker.DEFAULT_FETCH_LIMITS.
    sandbox_fetch_limits: dict = field(default_factory=dict)
    # ── MULTI-TENANCY (hosted only) ──────────────────────────────────────────────────────────
    # multi_tenant: this daemon serves MANY accounts, so state_dir/agents_dir/workspace/plugins_dir
    # are resolved PER CONNECTION under tenant_root/<account_id>/ instead of being process-global,
    # and every connection must present a session token that the accounts service can resolve
    # (fail-closed — an unattributable connection is refused, never pooled into a shared world).
    #
    # OFF by default and it must stay that way: a desktop daemon serves exactly one person, so the
    # globals are correct there and per-connection identity would be pure overhead. This is a
    # property of the DEPLOYMENT, which is why it is env-settable on the hosted task and nowhere
    # else. AGENTD_MULTI_TENANT=1.
    multi_tenant: bool = False
    # hosted: is this daemon serving people OTHER than its operator? DERIVED at load time from the
    # things that actually imply it (multi_tenant, an accounts URL), so every caller reads one
    # answer instead of re-deriving it and drifting. AGENTD_HOSTED forces it either way.
    #
    # It exists because a desktop install and a hosted one differ in KIND, not degree: on a desktop
    # an agent that runs a shell or hot-loads Python is the owner acting on their own machine; on a
    # shared container it is a stranger acting on everyone's. See domain/agent_availability.py.
    hosted: bool = False  # AGENTD_HOSTED
    # Which agents a HOSTED daemon withholds / permits, by id or trailing-* glob. Deny wins, then
    # allow, then the agent's own `requires_local` declaration. Both empty (the default) means the
    # authors' declarations decide alone. AGENTD_HOSTED_AGENTS_DENY / _ALLOW (comma-separated).
    hosted_agents_deny: tuple = ()
    hosted_agents_allow: tuple = ()
    # EXTRA shared read-only roots a HOSTED run may see, beyond the derived set (the caller's own
    # account subtree, the resolved agent's definition view, agents_dir + the plugin dirs) — for
    # a deployment that ships reference data outside those. Inert when hosted is false: a desktop
    # run's read scope is empty ( = unrestricted), so there is nothing to add to. See
    # infrastructure/user_state.tenant_scope, the ONE place that assembles the scope.
    hosted_read_roots: list = field(default_factory=list)
    # The accounts URL to ADVERTISE to browsers, when it differs from the one this daemon calls.
    # A hosted deployment reaches accounts over internal service DNS, which a visitor cannot
    # resolve — see client_accounts_url(). Empty everywhere else. AGENTD_PUBLIC_ACCOUNTS_URL.
    public_accounts_url: str = ""
    # Where tenant homes live. Empty = <AGENTD_HOME>/users. AGENTD_TENANT_ROOT.
    tenant_root: str = ""
    # How many tenants may be resident at once. A tenant costs MEMORY (its own loaded plugin
    # graph), not money — one process, no extra containers — so this is a memory budget and the
    # right value depends on how heavy the plugin set is. Least-recently-used UNREFERENCED tenants
    # are evicted past this; a tenant with live connections is never evicted. AGENTD_MAX_TENANTS.
    max_tenants: int = 50
    # Evict a tenant with no connections after this long. AGENTD_TENANT_IDLE_SECONDS.
    tenant_idle_seconds: int = 1800
    # sandbox_trusted_agents: agent ids whose PRIVATE tools (agents/<id>/plugins/) are trusted even
    # if the agent was installed from a package. The per-agent twin of the above — trust is normally
    # DERIVED (an agent is untrusted iff the marketplace ledger says it arrived in a .agentpkg), so
    # this is only for vouching for a specific publisher's agent. AGENTD_SANDBOX_TRUSTED_AGENTS.
    sandbox_trusted_agents: tuple = ()
    # sandbox_untrusted_agents: FORCE these agents' private tools to be treated as if they had been
    # installed from a package. A DEVELOPMENT switch, and the only knob here that TIGHTENS.
    #
    # It exists because trust is derived from the marketplace ledger, so the only way to see what a
    # buyer gets was to pack and install your own agent before every run — enough friction that the
    # sandbox stopped being exercised while it was being built. This makes the same code path run
    # against a local agent.
    #
    # It cannot weaken anything: the only transition it can cause is FIRST_PARTY ->
    # THIRD_PARTY_BUNDLE. Left on in production the worst case is an agent that is sandboxed when it
    # did not need to be — a capability that fails loudly, never an exposure. Operator-only
    # (config/env), like every sandbox knob: nothing inside a package can reach it.
    # AGENTD_SANDBOX_UNTRUSTED_AGENTS=comma,separated,ids — or `*` for EVERY agent, which is
    # what you want while developing: no id to add each time you build one.
    sandbox_untrusted_agents: tuple = ()
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
    # PER-AGENT OVERRIDES. Same nested-by-name shape as `plugins` above, so it needed no new
    # machinery: config.get already returns every exposed key and config.set already merges and
    # hot-applies every writable one. Shape:
    #   "agents": {"agent-builder": {"override_default": true, "model": "openai/gpt-5"}}
    # `override_default` (default TRUE) decides who wins, KEY BY KEY — the agent's value for
    # each knob it set, the daemon's for the rest. An agent with no entry here resolves to the
    # daemon's values exactly, which is how every agent behaves today. Only the knobs in
    # domain/agent_config.OVERRIDABLE_KEYS are honoured; provider keys and machine-wide
    # settings (port, paths) are deliberately absent. Resolved by agent_config.resolve().
    agents: dict = field(default_factory=dict)
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
    # DIAGNOSTICS UPLOAD (plan 5.1). Send a SHORT LIST of timing metrics — how long a run took, how
    # much of that was the model, whether it finished — to the platform's ingest service. This
    # daemon may be running on the user's own PC, where its stdout reaches nobody, so this is the
    # only way those numbers ever leave the machine.
    #
    # DEFAULT OFF, AND IT STAYS OFF UNTIL SOMEONE TICKS THE BOX. Metadata only: the forwarded
    # payload is names + numbers + correlation ids, gated by the same allowlist as every other
    # metric and re-checked by the receiver. Never message text, tool arguments, or file paths.
    # The URL comes from the distribution profile ([platform] ingest_url) or AGENTD_INGEST_URL;
    # with no URL this is inert regardless of the toggle.
    diagnostics_upload: bool = False  # AGENTD_DIAGNOSTICS_UPLOAD
    ingest_url: str = ""  # AGENTD_INGEST_URL / distribution [platform] ingest_url
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


def platform_discovered(config, name: str) -> str:
    """One address from the deployment's own discovery document, or "".

    Sits BETWEEN a machine's config and the build's baked per-service keys in every precedence
    chain below: an operator's env and a machine's config are still deliberate local overrides and
    must win, but a value the deployment publishes today beats one frozen into an installer months
    ago. That ordering is the whole point — the baked keys are a fallback for offline and for
    builds that predate discovery, not the source of truth.

    Imported lazily because agent_runtime.config is imported by nearly everything, and discovery
    pulls in httpx.
    """
    try:
        from agent_runtime.infrastructure import platform_discovery

        return platform_discovery.field(config, name)
    except Exception:  # noqa: BLE001 — discovery must never be able to break config resolution
        return ""


def accounts_api_base(config) -> str:
    """Where the accounts service lives: env > config > discovery > distribution profile.

    THREE SOURCES, ONE ANSWER, ONE FUNCTION. This used to be resolved in two places that looked
    in different sets of them: the accounts seam read env + config and ignored the profile, while
    the gateway's platform status read the profile and ignored env + config. So an accounts URL
    put in ``agentd.config.json`` configured the server side while every client was still told
    this build had no sign-in — which is how an agent ends up shipping a login screen that renders
    nothing at all.

    Same precedence ``registry_url`` and ``publish_target`` already use: an operator's environment
    beats a machine's config file, which beats whatever the build was shipped with.
    """
    acc = getattr(config, "accounts", None)
    acc = acc if isinstance(acc, dict) else {}
    profile = getattr(config, "distribution", None)
    return (
        (
            os.environ.get("AGENTD_ACCOUNTS_URL")
            or str(acc.get("api_base") or "")
            or platform_discovered(config, "auth_url")
            or str(getattr(profile, "accounts_url", "") or "")
        )
        .strip()
        .rstrip("/")
    )


def client_accounts_url(config) -> str:
    """The accounts URL to hand to a BROWSER, which is not always the one the daemon calls.

    On a hosted deployment `accounts_api_base` resolves to internal service DNS
    (`http://accounts.agentd.local:4100`) — exactly right for the daemon's own requests, and
    unreachable from a visitor's machine. Advertising it means a sign-in form that renders,
    accepts a password, and then fails to POST anywhere.

    So a deployment may declare its PUBLIC address separately (`AGENTD_PUBLIC_ACCOUNTS_URL`).
    Everywhere else — desktop, BYOK, a local checkout — nothing sets it and this is exactly
    `accounts_api_base`, because there the daemon and the browser reach the same host.
    """
    return str(getattr(config, "public_accounts_url", "") or "").strip().rstrip("/") or (
        accounts_api_base(config)
    )


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
    if os.environ.get("AGENTD_DIAGNOSTICS_UPLOAD"):
        cfg.diagnostics_upload = os.environ["AGENTD_DIAGNOSTICS_UPLOAD"].lower() not in (
            "0",
            "false",
            "no",
            "",
        )
    if os.environ.get("AGENTD_INGEST_URL"):
        cfg.ingest_url = os.environ["AGENTD_INGEST_URL"].strip().rstrip("/")
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
    if os.environ.get("AGENTD_RUN_IDLE_TIMEOUT"):
        cfg.run_idle_timeout_seconds = float(os.environ["AGENTD_RUN_IDLE_TIMEOUT"])
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
    if os.environ.get("AGENTD_MCP_WORKSHOP"):
        cfg.mcp_workshop = os.environ["AGENTD_MCP_WORKSHOP"].lower() not in ("0", "false", "no", "")
    if os.environ.get("AGENTD_AGENT_MESSAGING"):
        cfg.agent_messaging_enabled = os.environ["AGENTD_AGENT_MESSAGING"].lower() not in (
            "0",
            "false",
            "no",
            "",
        )
    # Multi-tenancy: hosted-only, env-only. Deliberately NOT readable from config.json — this
    # decides whether one person's files are reachable by another, and a setting that dangerous
    # should be a property of the deployment that starts the process, not of a file inside a
    # writable state directory that an agent could conceivably edit.
    if os.environ.get("AGENTD_SANDBOX_BACKEND"):
        cfg.sandbox_plugin_backend = os.environ["AGENTD_SANDBOX_BACKEND"].strip().lower()
    if os.environ.get("AGENTD_MULTI_TENANT"):
        cfg.multi_tenant = os.environ["AGENTD_MULTI_TENANT"].lower() not in ("0", "false", "no", "")
    if os.environ.get("AGENTD_TENANT_ROOT"):
        cfg.tenant_root = os.environ["AGENTD_TENANT_ROOT"].strip()
    if os.environ.get("AGENTD_PUBLIC_ACCOUNTS_URL"):
        cfg.public_accounts_url = os.environ["AGENTD_PUBLIC_ACCOUNTS_URL"].strip().rstrip("/")
    for _env, _field in (
        ("AGENTD_HOSTED_AGENTS_DENY", "hosted_agents_deny"),
        ("AGENTD_HOSTED_AGENTS_ALLOW", "hosted_agents_allow"),
    ):
        if os.environ.get(_env):
            setattr(cfg, _field, tuple(s.strip() for s in os.environ[_env].split(",") if s.strip()))
    # `cfg.hosted` is derived near the END of this function instead of here — it now depends on
    # the distribution profile, which is not loaded until further down. See "HOSTED, DERIVED LAST".
    for _env, _field in (
        ("AGENTD_MAX_TENANTS", "max_tenants"),
        ("AGENTD_TENANT_IDLE_SECONDS", "tenant_idle_seconds"),
    ):
        if os.environ.get(_env):
            try:
                setattr(cfg, _field, int(os.environ[_env]))
            except ValueError:
                logging.getLogger("agentd").warning("%s ignored: not an integer", _env)
    # THIRD-PARTY plugin code is sandboxed EVERYWHERE and ON BY DEFAULT (the dataclass default is
    # True). The trust boundary is PROVENANCE, not deployment shape: a marketplace agent's tools rode
    # in inside someone else's .agentpkg whether this daemon serves one person or a thousand. Our OWN
    # runtime plugins (v2/plugins — no _agent_id) and agents the user authored on this machine stay
    # FIRST_PARTY and run in-process; only code the installer laid down from a package is wrapped (see
    # classify_origin). The ONLY knob here is the OFF switch, which says so out loud because disabling
    # it means downloaded code runs with this daemon's own access. WHICH backend enforces it is the
    # host's capability, resolved separately (resolve_backend_name): a Windows box that cannot spawn a
    # child process degrades to in-process there, not here.
    if os.environ.get("AGENTD_SANDBOX_PLUGINS", "").strip().lower() in ("0", "false", "no"):
        cfg.sandbox_untrusted_plugins = False
        logging.getLogger("agentd").warning(
            "AGENTD_SANDBOX_PLUGINS disables the plugin sandbox — untrusted marketplace plugins "
            "will run IN-PROCESS with this daemon's access"
        )
    for _env, _field in (
        ("AGENTD_SANDBOX_NET_ALLOW", "sandbox_net_allow"),
        ("AGENTD_SANDBOX_NET_DENY", "sandbox_net_deny"),
    ):
        if os.environ.get(_env):
            setattr(cfg, _field, tuple(s.strip() for s in os.environ[_env].split(",") if s.strip()))
    if os.environ.get("AGENTD_SANDBOX_UNTRUSTED_AGENTS"):
        cfg.sandbox_untrusted_agents = tuple(
            s.strip()
            for s in os.environ["AGENTD_SANDBOX_UNTRUSTED_AGENTS"].split(",")
            if s.strip()
        )
    if cfg.sandbox_untrusted_agents:
        # LOUD, once, at boot. This is a development switch and the failure mode of forgetting it
        # is an agent that mysteriously cannot reach its own files — a message here is the
        # difference between a five-second fix and an afternoon.
        logging.getLogger("agentd").warning(
            "sandbox: FORCING untrusted classification for agent(s) %s (development switch "
            "sandbox_untrusted_agents) — their private tools run sandboxed as if installed",
            ", ".join(cfg.sandbox_untrusted_agents),
        )
    if os.environ.get("AGENTD_RUN_MODE"):
        cfg.run_mode = os.environ["AGENTD_RUN_MODE"].strip().lower()
    if os.environ.get("AGENTD_SANDBOX_TRUSTED_AGENTS"):
        cfg.sandbox_trusted_agents = tuple(
            s.strip()
            for s in os.environ["AGENTD_SANDBOX_TRUSTED_AGENTS"].split(",")
            if s.strip()
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
    # The PUBLISH side. Same env names the publisher tooling already documents, so a machine set
    # up to publish from the CLI needs nothing new for the tool to work.
    # PUBLISH TARGET, same three-tier resolution as registry_url above and for the same reason:
    #   AGENTD_PUBLISH_TARGET  >  config.json  >  the DISTRIBUTION PROFILE
    #
    # That third tier is the one that matters, and it was missing. An author installs the desktop
    # app, signs in, and presses Publish — they never open a .env and have no idea a publish
    # service exists. Requiring them to set this made the whole feature unreachable for exactly
    # the person it is for. The build already knows where its own marketplace is (it is the same
    # profile that carries accounts_url and registry_url), so the product supplies it.
    #
    # The env and file tiers stay on top for operators: publishing to an s3:// bucket or a local
    # directory is a release job, and it must be able to override whatever a build was baked with.
    if os.environ.get("AGENTD_PUBLISH_TARGET"):
        cfg.publish_target = os.environ["AGENTD_PUBLISH_TARGET"].strip()
    elif not cfg.publish_target:
        cfg.publish_target = cfg.distribution.publish_url
    if os.environ.get("AGENTD_PUBLISHER_KEYFILE"):
        cfg.publisher_keyfile = os.environ["AGENTD_PUBLISHER_KEYFILE"].strip()
    # The ENGINE a per-agent stub installs. Env doors for the same reason the registry has one: a
    # CI job or a container is configured by environment, not by editing a JSON file it does not
    # have. Empty stays empty — the registry index is the normal source.
    if os.environ.get("AGENTD_ENGINE_URL"):
        cfg.engine_installer_url = os.environ["AGENTD_ENGINE_URL"].strip()
    if os.environ.get("AGENTD_ENGINE_SHA256"):
        cfg.engine_installer_sha256 = os.environ["AGENTD_ENGINE_SHA256"].strip().lower()
    if os.environ.get("AGENTD_ENGINE_VERSION"):
        cfg.engine_version = os.environ["AGENTD_ENGINE_VERSION"].strip()
    # The publisher key needs the SAME env door as the url above, and for a specific reason.
    #
    # Both values normally arrive together, baked into a distribution profile by an installer. The
    # HOSTED daemon has neither: it is a container, and its whole configuration is task env. So the
    # only way to point a container at a registry was AGENTD_REGISTRY — which carries the url and
    # NOT the key, silently downgrading every download from "signed by the publisher we pinned" to
    # "the checksum matches whatever index.json claimed". Those are very different guarantees: a
    # checksum only proves the .agentpkg is the file the index described, and anyone who can
    # rewrite index.json rewrites the checksum with it. On a public-read bucket that is exactly
    # the attack the signature exists to stop, so the key gets a door of its own.
    #
    # Set on the profile rather than a new Config field so there stays ONE place that answers
    # "which publisher do we trust" (marketplace/factory.py reads config.distribution.publisher_key).
    # Stripped BEFORE the emptiness test, not after: `AGENTD_PUBLISHER_KEY="  "` is truthy, so
    # testing the raw value would accept a blank and overwrite a real pinned key with "" — turning
    # verification off on a build whose profile had it on. A whitespace-only value means unset.
    publisher_key_env = os.environ.get("AGENTD_PUBLISHER_KEY", "").strip()
    if publisher_key_env:
        from dataclasses import replace

        cfg.distribution = replace(cfg.distribution, publisher_key=publisher_key_env)
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
    if os.environ.get("AGENTD_APP_HOST_SUFFIX"):
        # A bare base domain ("example.com"). Normalized here once — lowercase, no leading
        # dot — so the gateway's per-request comparison never has to re-clean it.
        cfg.app_host_suffix = os.environ["AGENTD_APP_HOST_SUFFIX"].strip().lower().lstrip(".")

    # HOSTED, DERIVED LAST — after the distribution profile, so it sees every source.
    #
    # A daemon is "hosted" when it serves people other than its operator. Two ways that is true:
    # multi_tenant (per-connection isolation), or accounts being ENFORCED, which means every
    # connection must resolve to an account instead of presenting the machine token.
    #
    # NOTE WHAT DOES NOT MAKE IT HOSTED: merely knowing where people sign in. An accounts URL that
    # arrived from config or the profile ADVERTISES sign-in — it is what lets an agent's UI show a
    # login at all — and that must stay free of any effect on how existing connections
    # authenticate. Turning those two into one flag is what made "configure sign-in" and "lock
    # yourself out of your own daemon" the same action.
    #
    # The env var is the escape hatch in both directions: a private daemon behind accounts that is
    # genuinely single-user can set AGENTD_HOSTED=0.
    _accounts = cfg.accounts if isinstance(cfg.accounts, dict) else {}
    _accounts_enforced = bool(accounts_api_base(cfg)) and (
        os.environ.get("AGENTD_ACCOUNTS_URL") is not None or bool(_accounts.get("enabled"))
    )
    cfg.hosted = cfg.multi_tenant or _accounts_enforced
    if os.environ.get("AGENTD_HOSTED"):
        cfg.hosted = os.environ["AGENTD_HOSTED"].lower() not in ("0", "false", "no", "")
    # The env hatch may downgrade the accounts-enforced SINGLE-USER case (a private daemon behind
    # login, one human — fences off is fine). It must NEVER win over multi_tenant: a daemon that
    # genuinely serves multiple tenants derives every fs/exec fence from cfg.hosted (tenant_scope
    # returns unrestricted when it is false), so hosted=false there would open every tenant's files
    # to every other in one flag. That contradiction fails CLOSED — hosted forced on — not open.
    if cfg.multi_tenant and not cfg.hosted:
        logging.getLogger("agentd.config").error(
            "AGENTD_HOSTED=0 ignored: multi_tenant is set, so tenant fences stay ON — a "
            "multi-tenant daemon cannot run unfenced without exposing every tenant to every other."
        )
        cfg.hosted = True

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

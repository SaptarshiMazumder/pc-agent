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
class Config:
    # The agent's persona name (how it introduces itself + identifies in the prompt).
    # Single source of truth: the server owns it; clients fetch it via the `hello`
    # handshake. Override with AGENTD_AGENT_NAME.
    agent_name: str = "JARVIS"
    model: str = "gemini/gemini-3.1-pro-preview"
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
    brave_api_key: str | None = None
    # Explicit web_search provider chain order (e.g. ["gemini","brave","duckduckgo"]).
    # None = auto: gemini (if on a Gemini model + key) -> brave (if key) -> duckduckgo.
    # Override with AGENTD_SEARCH_PROVIDERS (comma-separated).
    search_providers: list[str] | None = None
    browser_headless: bool = True
    # Persistent browser profile: keep cookies/logins on disk (<state_dir>/browser-profile)
    # so the `browser` tool stays SIGNED IN across runs. Log in once (headed) via
    # `python -m agentd.main.browser_login`. AGENTD_BROWSER_PERSISTENT=0 to disable.
    browser_persistent: bool = True
    exec_timeout_sec: int = 1800
    max_turns: int = 100  # agent-loop iteration cap (LLM turns per run); override AGENTD_MAX_TURNS
    agent_id: str = "main"

    # --- reliability / guardrails (applied to EVERY tool via GuardedTool) -------
    # Per-tool effective values resolve: tool_overrides[name] > the tool's own
    # declared default (default_* class attr) > these globals.
    tool_timeout_default: float = 300.0   # wall-clock per tool call (AGENTD_TOOL_TIMEOUT); per-tool null = no wrapper
    tool_retries_default: int = 0         # extra attempts on transient errors (AGENTD_TOOL_RETRIES)
    # Per-tool overrides, e.g. {"computer": {"timeout_sec": 900}, "exec": {"timeout_sec": null},
    # "web_search": {"timeout_sec": 20, "max_retries": 3, "retryable": true}}. JSON config only.
    tool_overrides: dict = field(default_factory=dict)
    # Loop/LLM-level timeouts.
    llm_idle_timeout_seconds: float = 120.0    # abort a model stream silent for this long (AGENTD_LLM_IDLE_TIMEOUT)
    llm_request_timeout_seconds: float = 600.0  # hard ceiling per model call (AGENTD_LLM_REQUEST_TIMEOUT)

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
        key, value = key.strip(), value.strip().strip("'\"")
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
                    if key in ("workspace", "state_dir", "skills_dir"):
                        value = Path(value)
                    setattr(cfg, key, value)
            break

    if os.environ.get("AGENTD_AGENT_NAME"):
        cfg.agent_name = os.environ["AGENTD_AGENT_NAME"]
    if os.environ.get("AGENTD_MODEL"):
        cfg.model = os.environ["AGENTD_MODEL"]
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
    if os.environ.get("AGENTD_HEADLESS"):
        cfg.browser_headless = os.environ["AGENTD_HEADLESS"].lower() not in ("0", "false", "no")
    if os.environ.get("AGENTD_BROWSER_PERSISTENT"):
        cfg.browser_persistent = (
            os.environ["AGENTD_BROWSER_PERSISTENT"].lower() not in ("0", "false", "no", "")
        )
    if os.environ.get("BRAVE_API_KEY"):
        cfg.brave_api_key = os.environ["BRAVE_API_KEY"]
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

    cfg.workspace = Path(cfg.workspace).resolve()
    cfg.state_dir = Path(cfg.state_dir)
    cfg.skills_dir = Path(cfg.skills_dir)
    return cfg

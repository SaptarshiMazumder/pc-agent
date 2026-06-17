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
    exec_timeout_sec: int = 1800
    max_turns: int = 100  # agent-loop iteration cap (LLM turns per run); override AGENTD_MAX_TURNS
    agent_id: str = "main"


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
    if os.environ.get("BRAVE_API_KEY"):
        cfg.brave_api_key = os.environ["BRAVE_API_KEY"]
    if os.environ.get("AGENTD_SEARCH_PROVIDERS"):
        cfg.search_providers = [
            s.strip() for s in os.environ["AGENTD_SEARCH_PROVIDERS"].split(",") if s.strip()
        ]

    cfg.workspace = Path(cfg.workspace).resolve()
    cfg.state_dir = Path(cfg.state_dir)
    cfg.skills_dir = Path(cfg.skills_dir)
    return cfg

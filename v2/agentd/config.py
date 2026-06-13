"""Configuration: optional JSON file + environment overrides.

Env vars: AGENTD_MODEL, AGENTD_HOST, AGENTD_PORT, AGENTD_WORKSPACE,
AGENTD_STATE_DIR, AGENTD_HEADLESS, BRAVE_API_KEY. Provider API keys
(ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY, ...) are read by
LiteLLM directly from the environment.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    model: str = "gemini/gemini-2.5-flash"
    host: str = "127.0.0.1"
    port: int = 8787
    workspace: Path = field(default_factory=Path.cwd)
    state_dir: Path = field(default_factory=lambda: Path.home() / ".agentd")
    brave_api_key: str | None = None
    browser_headless: bool = True
    exec_timeout_sec: int = 1800
    max_turns: int = 50
    agent_id: str = "main"


def _load_dotenv() -> None:
    """Load KEY=VALUE lines from ./.env or ../.env into os.environ (no override)."""
    for candidate in (Path(".env"), Path("..") / ".env"):
        if not candidate.is_file():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip("'\"")
            if key and value and key not in os.environ:
                os.environ[key] = value
        break


def load_config(path: Path | None = None) -> Config:
    _load_dotenv()
    cfg = Config()

    candidates = [path] if path else [
        Path(os.environ.get("AGENTD_CONFIG", "")) if os.environ.get("AGENTD_CONFIG") else None,
        Path("agentd.config.json"),
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            data = json.loads(candidate.read_text(encoding="utf-8"))
            for key, value in data.items():
                if hasattr(cfg, key):
                    if key in ("workspace", "state_dir"):
                        value = Path(value)
                    setattr(cfg, key, value)
            break

    if os.environ.get("AGENTD_MODEL"):
        cfg.model = os.environ["AGENTD_MODEL"]
    if os.environ.get("AGENTD_HOST"):
        cfg.host = os.environ["AGENTD_HOST"]
    if os.environ.get("AGENTD_PORT"):
        cfg.port = int(os.environ["AGENTD_PORT"])
    if os.environ.get("AGENTD_WORKSPACE"):
        cfg.workspace = Path(os.environ["AGENTD_WORKSPACE"])
    if os.environ.get("AGENTD_STATE_DIR"):
        cfg.state_dir = Path(os.environ["AGENTD_STATE_DIR"])
    if os.environ.get("AGENTD_HEADLESS"):
        cfg.browser_headless = os.environ["AGENTD_HEADLESS"].lower() not in ("0", "false", "no")
    if os.environ.get("BRAVE_API_KEY"):
        cfg.brave_api_key = os.environ["BRAVE_API_KEY"]

    cfg.workspace = Path(cfg.workspace).resolve()
    cfg.state_dir = Path(cfg.state_dir)
    return cfg

"""Where agentd's files live — REPO checkout vs INSTALLED package, one resolver.

Two runtime modes, detected once from the package itself (no flag, no config):

* **repo mode** — running from a v2/ checkout (`python -m agentd`, tests). Everything
  anchors to the repo root exactly as before: plugins/ agents/ .agentd/ SOUL.md.
* **packaged mode** — running from an installed wheel (`pip install agentd` ->
  `agentd`/`jarvis`). The shipped built-ins live INSIDE the package (the wheel build
  copies plugins/ to ``agentd/_builtin_plugins/`` and starter data to ``agentd/_data/``),
  and everything user-serviceable lives under ``~/.agentd`` (override: AGENTD_HOME):

      ~/.agentd/
        config.json        # the JSON config (repo mode: v2/agentd.config.json)
        .env               # user secrets (loaded in BOTH modes, never overrides)
        gateway.json       # daemon rendezvous: {host, port, pid, token, version}
        SOUL.md            # editable persona (seeded from the packaged default)
        agents/<id>/       # agent definitions (+ agents/main/skills = shared library)
        plugins/<id>/      # user drop-in + marketplace-installed plugins
        state/             # sessions, memory, ledgers (the state_dir)
        licenses/          # signed entitlement licenses (commercial bundles)
        logs/              # daemon logs (the detached daemon writes here)

The mode decides only DEFAULTS — every path stays overridable exactly as before
(AGENTD_* env vars and the JSON config win). Everything here is stdlib-only and
import-cheap: config.py calls these at load time.
"""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
# In a checkout this is v2/ (the historical V2_ROOT); in an installed wheel it is
# site-packages/ — which is why packaged mode must NOT anchor anything to it.
REPO_ROOT = PACKAGE_DIR.parent

# Placed inside the package by the wheel build (pyproject force-include). Their
# presence IS the packaged-mode marker: a checkout never has them.
PACKAGED_BUILTINS_DIR = PACKAGE_DIR / "_builtin_plugins"
PACKAGED_DATA_DIR = PACKAGE_DIR / "_data"


def is_packaged() -> bool:
    """True when running from an installed wheel (built-ins live inside the package)."""
    return PACKAGED_BUILTINS_DIR.is_dir()


def user_home() -> Path:
    """The per-user agentd home (packaged mode's anchor). AGENTD_HOME overrides."""
    override = os.environ.get("AGENTD_HOME", "").strip()
    return Path(override).expanduser() if override else Path.home() / ".agentd"


# ---- mode-dependent defaults (config.py reads these; env/JSON still win) ----------


def default_state_dir() -> Path:
    return user_home() / "state" if is_packaged() else REPO_ROOT / ".agentd"


def default_agents_dir() -> Path:
    return user_home() / "agents" if is_packaged() else REPO_ROOT / "agents"


def default_skills_dir() -> Path:
    """The shared skills library = MAIN's skills, wherever agents live in this mode."""
    return default_agents_dir() / "main" / "skills"


def default_user_plugins_dir() -> Path:
    """Where drop-in (and marketplace-installed) plugins live."""
    return user_home() / "plugins" if is_packaged() else REPO_ROOT / "plugins"


def builtin_plugins_dir() -> Path:
    """The SHIPPED built-in bundles — packaged inside the wheel, else the repo dir."""
    return PACKAGED_BUILTINS_DIR if is_packaged() else REPO_ROOT / "plugins"


def config_candidates() -> list[Path]:
    """Config file search order (first hit wins): explicit env > cwd > repo (checkout
    only) > user home. The user-home config is last so a checkout keeps its own."""
    candidates: list[Path] = []
    env = os.environ.get("AGENTD_CONFIG", "").strip()
    if env:
        candidates.append(Path(env))
    candidates.append(Path("agentd.config.json"))
    if not is_packaged():
        candidates.append(REPO_ROOT / "agentd.config.json")
    candidates.append(user_config_file())
    return candidates


def user_config_file() -> Path:
    return user_home() / "config.json"


def default_config_write_path() -> Path:
    """Where to WRITE config when none was found (onboarding, best-effort persists)."""
    return user_config_file() if is_packaged() else REPO_ROOT / "agentd.config.json"


def env_files() -> list[Path]:
    """Dotenv files to load (no override): the checkout's .env, then the user's."""
    files = [] if is_packaged() else [REPO_ROOT / ".env"]
    files.append(user_home() / ".env")
    return files


# ---- machine-level rendezvous + user-space folders (mode-independent) -------------


def gateway_file() -> Path:
    """The daemon rendezvous file — ALWAYS under the user home, in both modes, so
    every client on the machine finds the one running daemon the same way."""
    return user_home() / "gateway.json"


def logs_dir() -> Path:
    return user_home() / "logs"


def licenses_dir() -> Path:
    return user_home() / "licenses"


def distribution_candidates() -> list[Path]:
    """distribution.toml search order: explicit env > user home > packaged flavor file.
    No file at all => the open profile (everything provisioned, store enabled)."""
    candidates: list[Path] = []
    env = os.environ.get("AGENTD_DISTRIBUTION", "").strip()
    if env:
        candidates.append(Path(env))
    candidates.append(user_home() / "distribution.toml")
    candidates.append(PACKAGED_DATA_DIR / "distribution.toml")
    return candidates


def packaged_soul_file() -> Path:
    """The default SOUL.md shipped in the wheel (seeded to ~/.agentd on first run)."""
    return PACKAGED_DATA_DIR / "SOUL.md"


def packaged_starter_agents_dir() -> Path:
    """Starter agent content shipped in the wheel (main's shared skills library)."""
    return PACKAGED_DATA_DIR / "agents"


def ensure_user_layout() -> Path:
    """Create the ~/.agentd skeleton (idempotent). Returns the home dir."""
    home = user_home()
    for d in (home, home / "agents", home / "plugins", home / "state",
              licenses_dir(), logs_dir()):
        d.mkdir(parents=True, exist_ok=True)
    return home

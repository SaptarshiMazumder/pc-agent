"""Where agentd's files live — REPO checkout vs INSTALLED package, one resolver.

Two runtime modes, detected once from the package itself (no flag, no config):

* **repo mode** — running from a v2/ checkout (`python -m agent_runtime`, tests). Everything
  anchors to the repo root exactly as before: plugins/ agents/ .agentd/ SOUL.md.
* **packaged mode** — running from an installed wheel (`pip install agentd` ->
  `agentd`/`jarvis`). The shipped built-ins live INSIDE the package (the wheel build
  copies plugins/ to ``agent_runtime/_builtin_plugins/`` and starter data to ``agent_runtime/_data/``),
  and everything user-serviceable lives under ``~/.agentd`` (override: AGENTD_HOME):

      ~/.agentd/
        config.json        # the JSON config (repo mode: v2/agent_runtime.config.json)
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


def cache_dir() -> Path:
    """Where third-party libraries may write their downloaded caches.

    ALWAYS under the user home, because the alternative is the INSTALL directory and that is not
    guaranteed writable. A per-machine install lands in C:\\Program Files (an enterprise HKLM
    deployment does so by design, and a silent install can choose it), and the daemon runs as an
    ordinary user — so anything caching next to its own code fails with EACCES on the first call
    that needs it."""
    return user_home() / "cache"


def redirect_library_caches() -> None:
    """Point libraries that cache into their own package directory at cache_dir() instead.

    litellm is the one that bites: it sets TIKTOKEN_CACHE_DIR to
    ``site-packages/litellm/litellm_core_utils/tokenizers`` at import time, and tiktoken then
    writes each encoding it fetches there — via a `<uuid>.tmp` it renames. On a per-machine
    install that is a read-only directory, so the FIRST message a user sends dies with
    ``[Errno 13] Permission denied: ...\\tokenizers\\<uuid>.tmp``. litellm supports exactly one
    override for this, CUSTOM_TIKTOKEN_CACHE_DIR, and honours it before touching its own folder.

    `setdefault`, so an operator who has already pointed these somewhere (a shared read-only
    cache baked into a container, say) keeps their choice. Must run BEFORE litellm is imported,
    which is why it is called from the package __init__ rather than from an entry point: the
    daemon, the CLI, the model proxy and the tests all reach litellm by different routes."""
    if os.environ.get("CUSTOM_TIKTOKEN_CACHE_DIR", "").strip():
        return
    target = cache_dir() / "tiktoken"
    os.environ["CUSTOM_TIKTOKEN_CACHE_DIR"] = str(target)
    _seed_tiktoken_cache(target)


def _seed_tiktoken_cache(target: Path) -> None:
    """Copy litellm's SHIPPED encodings into the new cache the first time.

    Redirecting the cache would otherwise cost the offline capability the bundled directory
    exists to provide (litellm #1071): the encodings ride along in the wheel so a first token
    count never has to reach openaipublic.blob.core.windows.net, which is exactly the host a
    locked-down network blocks. Seeding keeps that property AND makes the location writable.

    `find_spec` rather than `import litellm` — importing it here is what we are racing to get
    ahead of. Best-effort throughout: a failure to seed leaves a cache that fills itself from the
    network, which is the normal tiktoken behaviour, not a broken state."""
    import importlib.util
    import shutil

    if target.exists():
        return  # already seeded (or already in use) — never overwrite a live cache
    try:
        spec = importlib.util.find_spec("litellm")
        origin = getattr(spec, "origin", None) if spec is not None else None
        if not origin:
            return
        bundled = Path(origin).parent / "litellm_core_utils" / "tokenizers"
        if not bundled.is_dir():
            return
        target.mkdir(parents=True, exist_ok=True)
        for item in bundled.iterdir():
            # The cache holds hash-named encoding blobs. Skip the package's own machinery
            # (__init__.py, __pycache__) and the json litellm reads from its own path.
            if item.is_file() and not item.suffix:
                shutil.copyfile(item, target / item.name)
    except OSError:
        return


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


def packaged_default_config() -> Path:
    """The default config.json template shipped in the wheel (full model_catalog + neutral
    knobs). first_run seeds the user config from this so a fresh install has a real model
    menu instead of a bare stub. In a checkout the repo's config.example.json is the source."""
    return PACKAGED_DATA_DIR / "config.default.json" if is_packaged() else REPO_ROOT / "config.example.json"


def packaged_starter_agents_dir() -> Path:
    """Starter agent content shipped in the wheel (main's shared skills library)."""
    return PACKAGED_DATA_DIR / "agents"


def launcher_path(base: str | None = None) -> str:
    """The PATH a CHILD PROCESS of agentd should get: this interpreter's script directory
    first, then everything already there.

    Stdio MCP plugins name their launcher as a bare command (``uvx``, ``npx``). Those are
    declared as dependencies and pip-installed alongside agentd — which puts them in
    ``<prefix>/Scripts`` (Windows) or ``<prefix>/bin`` (POSIX). Neither is added to PATH just
    by RUNNING that interpreter, so in a packaged install the launcher sits on disk, correctly
    installed, and resolves to nothing.

    ONE definition, because two places need the same answer and they must not disagree: the
    plugin-compatibility gate (`[requires] bins`) decides whether to load a plugin, and the
    subprocess spawner decides where to find its command. When those used different PATHs, a
    declared-and-installed launcher could be judged missing and the plugin skipped.
    """
    import sys
    import sysconfig

    current = os.environ.get("PATH", "") if base is None else base
    scripts = sysconfig.get_path("scripts")
    known = current.split(os.pathsep)
    extra = [
        d
        for d in (scripts, os.path.dirname(sys.executable))
        if d and os.path.isdir(d) and d not in known
    ]
    return os.pathsep.join([*extra, current]) if extra else current


def ensure_user_layout() -> Path:
    """Create the ~/.agentd skeleton (idempotent). Returns the home dir."""
    home = user_home()
    for d in (home, home / "agents", home / "plugins", home / "state", licenses_dir(), logs_dir()):
        d.mkdir(parents=True, exist_ok=True)
    return home

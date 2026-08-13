"""First-run onboarding (M1) — make a fresh install usable in under a minute.

Packaged mode, no config yet: create the ~/.agentd skeleton, seed the editable
persona (SOUL.md) + the starter shared skills from the wheel's _data, then (on a real
TTY) run a tiny wizard: pick a provider, paste an API key, write config.json + .env,
and verify the key with a 1-token ping. Non-interactive (no TTY): seed everything,
write a default config, and say exactly what's missing — never hang waiting for input.

Repo mode: only the idempotent layout/seed steps run; a checkout keeps its own
agentd.config.json / .env untouched.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from agent_runtime import runtime_paths

# fallback model when onboarding can't ask (no TTY / hosted flavor); config-overridable seed
_DEFAULT_MODEL = "gemini/gemini-2.5-flash"

# provider menu: id -> (env var LiteLLM reads, default model, key URL)
PROVIDERS: dict[str, tuple[str, str, str]] = {
    "gemini": ("GEMINI_API_KEY", "gemini/gemini-2.5-flash", "https://aistudio.google.com/apikey"),
    "anthropic": (
        "ANTHROPIC_API_KEY",
        "anthropic/claude-sonnet-4-5",
        "https://console.anthropic.com/",
    ),
    "openai": ("OPENAI_API_KEY", "openai/gpt-5.1", "https://platform.openai.com/api-keys"),
    "deepseek": ("DEEPSEEK_API_KEY", "deepseek/deepseek-chat", "https://platform.deepseek.com/"),
}


def is_onboarded() -> bool:
    """Onboarded = a config file exists anywhere in the search order."""
    return any(c.is_file() for c in runtime_paths.config_candidates())


def seed_user_layout() -> None:
    """Idempotent: the ~/.agentd skeleton + packaged starter content (SOUL.md, main's
    shared skills). Two rules, split by whose files they are:

      * THE USER'S trees (SOUL.md, main's shared skills library) — additive only, never
        overwrite: their edits are the point.
      * OUR curated agents (a shipped dir with an agent.toml) — REFRESHED on upgrade. "Never
        overwrite" here meant the seeded agent-builder kept its first-ever SDK forever, so
        every engine upgrade ran a new daemon under an ancient sign-in gate ("this device did
        not activate"). The ownership record is what makes refreshing safe: owner=platform
        says nobody but us writes there — except workspace/, which is the USER's data inside
        our agent and survives untouched. A dir with no record or someone else's stays on the
        additive rule exactly as before.
    """
    home = runtime_paths.ensure_user_layout()
    soul_src = runtime_paths.packaged_soul_file()
    soul_dst = home / "SOUL.md"
    if soul_src.is_file() and not soul_dst.exists():
        shutil.copyfile(soul_src, soul_dst)
    starter = runtime_paths.packaged_starter_agents_dir()
    if starter.is_dir():
        agents_dir = home / "agents" if runtime_paths.is_packaged() else None
        if agents_dir is not None:
            for shipped in sorted(starter.iterdir()):
                if shipped.is_dir() and (shipped / "agent.toml").is_file():
                    _seed_curated_agent(shipped, agents_dir / shipped.name)
                else:
                    _copy_missing(shipped, agents_dir / shipped.name)


def _copy_missing(src: Path, dst_root: Path) -> None:
    """Additive copy: bring what is absent, never touch what exists."""
    entries = src.rglob("*") if src.is_dir() else [src]
    for item in entries:
        if not item.is_file():
            continue
        dst = dst_root / item.relative_to(src) if src.is_dir() else dst_root
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(item, dst)


def _seed_curated_agent(shipped: Path, dst: Path) -> None:
    """Install a SHIPPED default agent, refreshing an existing copy only when it is OURS."""
    from agent_runtime.domain import ownership
    from agent_runtime.infrastructure.agents import ownership_store

    record = ownership_store.read(dst)
    if dst.exists() and (record is None or record.owner != ownership.PLATFORM_OWNER):
        # Record-less (pre-record install, possibly user-edited) or claimed by someone: their
        # dir. Additive only — refreshing would overwrite edits we cannot prove are ours.
        _copy_missing(shipped, dst)
        return
    for item in shipped.rglob("*"):
        if not item.is_file():
            continue
        rel = item.relative_to(shipped)
        if rel.parts and rel.parts[0] == "workspace":
            continue  # the user's data inside our agent — never ours to replace
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(item, target)
    # Stated as data: ours, curated — which is exactly what authorizes the next refresh, and
    # what makes a signed-in user's Publish button refuse to re-sign our agent as theirs.
    ownership_store.write(
        dst, ownership.OwnershipRecord(owner=ownership.PLATFORM_OWNER, origin=ownership.CURATED)
    )


def _write_config(model: str) -> Path:
    """Write the user's config.json, SEEDED from the shipped default template (full
    model_catalog + neutral knobs) so a fresh install has a real model picker — not a bare
    {model} stub. The onboarded model (wizard pick / default) overrides the template's."""
    path = runtime_paths.user_config_file()
    template = runtime_paths.packaged_default_config()
    try:
        config = json.loads(template.read_text(encoding="utf-8")) if template.is_file() else {}
    except (OSError, ValueError):
        config = {}  # unreadable/corrupt template — fall back to a minimal but valid config
    config.setdefault("memory_enabled", True)
    config["model"] = model  # the onboarded choice wins over the template default
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return path


def _append_env(var: str, value: str) -> Path:
    env_path = runtime_paths.user_home() / ".env"
    existing = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""
    if not any(
        line.split("=", 1)[0].strip() == var for line in existing.splitlines() if "=" in line
    ):
        with env_path.open("a", encoding="utf-8") as f:
            f.write(f"{var}={value}\n")
    return env_path


def _verify_key(model: str) -> tuple[bool, str]:
    """1-token ping through LiteLLM so a typo'd key fails HERE, not mid-first-chat."""
    try:
        import litellm

        litellm.completion(model=model, max_tokens=1, messages=[{"role": "user", "content": "hi"}])
        return True, "key verified"
    except Exception as e:  # noqa: BLE001 — any provider error = not verified
        return False, str(e)[:200]


def _wizard() -> bool:
    print("\nWelcome to agentd — first-run setup (once; edit ~/.agentd/config.json later).\n")
    ids = list(PROVIDERS)
    for i, pid in enumerate(ids, 1):
        print(f"  {i}. {pid}   (default model: {PROVIDERS[pid][1]})")
    print(f"  {len(ids) + 1}. other (type any LiteLLM model id; bring your own env key)")
    choice = input(f"\nPick a provider [1-{len(ids) + 1}] (default 1): ").strip() or "1"
    try:
        index = max(1, min(len(ids) + 1, int(choice))) - 1
    except ValueError:
        index = 0
    if index < len(ids):
        provider = ids[index]
        env_var, default_model, key_url = PROVIDERS[provider]
        model = input(f"Model [{default_model}]: ").strip() or default_model
        import getpass

        print(f"\nGet a key at: {key_url}")
        key = getpass.getpass(f"{env_var} (input hidden, Enter to skip): ").strip()
        if key:
            _append_env(env_var, key)
            import os

            os.environ.setdefault(env_var, key)
    else:
        model = input("LiteLLM model id (e.g. groq/llama-3.3-70b): ").strip()
        if not model:
            print("No model given — writing default config; edit ~/.agentd/config.json.")
            model = _DEFAULT_MODEL
    config_path = _write_config(model)
    print(f"\nWrote {config_path}")
    ok, detail = _verify_key(model)
    print(f"Key check: {'OK' if ok else 'FAILED - ' + detail}")
    if not ok:
        print("You can fix the key in ~/.agentd/.env and re-check with `agentd doctor`.")
    return True


def ensure_onboarded() -> bool:
    """Main entry — returns True when a usable config exists (possibly just created)."""
    seed_user_layout()
    if is_onboarded():
        return True
    if not runtime_paths.is_packaged():
        # a checkout without agentd.config.json: loud pointer instead of a wizard —
        # dev configs are project files, not ~/.agentd files.
        print("No agentd.config.json found in the checkout; copy config.example.json to start.")
        return False
    # Hosted flavor (distribution declares a platform model proxy): model calls run on
    # PLATFORM keys once the user signs in through the app — the BYOK provider/key wizard
    # would only mislead. Seed the default config and skip it entirely.
    from agent_runtime.distribution import load_profile

    if load_profile().model_proxy_url:
        _write_config(_DEFAULT_MODEL)
        print("Hosted install — sign in from the app; model access uses platform keys.")
        return True
    if sys.stdin.isatty() and sys.stdout.isatty():
        return _wizard()
    _write_config(_DEFAULT_MODEL)
    print(
        "No TTY for setup — wrote ~/.agentd/config.json with defaults. "
        "Set your API key in ~/.agentd/.env (e.g. GEMINI_API_KEY=...), then run `agentd doctor`."
    )
    return True

"""`agentd doctor` — is this install healthy, and if not, exactly what to do.

Checks are cheap and read-only: runtime mode + paths, config + model + provider key,
the daemon, and the optional heavy runtime deps (playwright browsers, ffmpeg, java,
node) that individual tools degrade without. Exit code 0 = all required checks pass."""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

OK, WARN, FAIL = "ok", "warn", "FAIL"

# model prefix -> env var(s) that satisfy it (LiteLLM reads these directly)
_KEY_VARS = {
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "deepseek": ("DEEPSEEK_API_KEY",),
    "groq": ("GROQ_API_KEY",),
    "mistral": ("MISTRAL_API_KEY",),
    "xai": ("XAI_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",),
}


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("doctor", help="check this install: config, keys, deps, daemon")
    parser.set_defaults(func=run)


def _check_playwright_browsers() -> tuple[str, str]:
    try:
        import playwright  # noqa: F401
    except ImportError:
        return WARN, "playwright not installed (browser tool off)"
    root = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "") or
                (Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ms-playwright"
                 if os.name == "nt" else Path.home() / ".cache" / "ms-playwright"))
    if root.is_dir() and any(d.name.startswith("chromium") for d in root.iterdir()):
        return OK, f"chromium present ({root})"
    return WARN, "no chromium — run `playwright install chromium` to enable the browser tool"


def run(args: argparse.Namespace) -> int:
    from agentd import lifecycle, runtime_paths
    from agentd.config import load_config

    rows: list[tuple[str, str, str]] = []

    mode = "packaged" if runtime_paths.is_packaged() else "repo checkout"
    rows.append(("runtime", OK, f"{mode}; home={runtime_paths.user_home()}"))

    config = load_config()
    if config.config_path:
        rows.append(("config", OK, config.config_path))
    else:
        rows.append(("config", FAIL, "no config file — run `agentd` once to onboard"))

    model = getattr(config, "model", "")
    provider = model.split("/", 1)[0] if "/" in model else "gemini"
    key_vars = _KEY_VARS.get(provider)
    if key_vars is None:
        rows.append(("api key", WARN, f"model '{model}': unknown provider — ensure its env key is set"))
    elif any(os.environ.get(v) for v in key_vars):
        rows.append(("api key", OK, f"{provider}: {' or '.join(key_vars)} present"))
    else:
        rows.append(("api key", FAIL,
                     f"model '{model}' needs {' or '.join(key_vars)} (set it in "
                     f"{runtime_paths.user_home() / '.env'})"))

    info = lifecycle.find_running()
    rows.append(("daemon", OK if info else WARN,
                 f"running (pid {info.pid}, {info.ws_url})" if info
                 else "not running — starts automatically with `agentd`"))

    rows.append(("playwright", *_check_playwright_browsers()))
    for binary, why in (("ffmpeg", "video/tts tools"), ("java", "plantuml diagrams"),
                        ("node", "desktop shell dev")):
        path = shutil.which(binary)
        rows.append((binary, OK if path else WARN, path or f"not on PATH ({why} degrade)"))

    try:
        probe = runtime_paths.user_home() / ".doctor-probe"
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        rows.append(("home writable", OK, str(runtime_paths.user_home())))
    except OSError as e:
        rows.append(("home writable", FAIL, str(e)))

    width = max(len(r[0]) for r in rows)
    for name, status, detail in rows:
        print(f"  {name.ljust(width)}  [{status:^4}]  {detail}")
    failures = [r for r in rows if r[1] == FAIL]
    print(f"\n{len(rows) - len(failures)}/{len(rows)} checks passing"
          + (f" — fix the {len(failures)} FAIL(s) above" if failures else " — you're good"))
    return 1 if failures else 0

"""
Wrapper around the `agent-browser` CLI — OpenClaw's actual browser engine.

Method (identical to OpenClaw): Chrome via CDP, accessibility-tree snapshots
with stable @eN refs, no pixels. The core loop the agent composes:
    open(url) -> snapshot() -> act on @refs (click/fill/press) -> re-snapshot

The browser runs as a warm daemon that persists across commands. A COLD `open`
spawns/attaches that daemon and blocks a captured subprocess, so we launch it
detached and poll `get url`; once warm, every command returns JSON we capture.
"""
import json
import os
import shutil
import subprocess
import time
import urllib.parse
from pathlib import Path

# npm installs it as agent-browser.CMD on Windows — resolve the full path so a
# bare-name subprocess call doesn't FileNotFoundError.
CLI = shutil.which("agent-browser") or "agent-browser"
_T = 60
_DETACH = {"creationflags": 0x08000000} if os.name == "nt" else {}   # CREATE_NO_WINDOW

# How we run the engine — matched to OpenClaw's ACTUAL default (verified in its
# source: the default "openclaw" profile is an isolated, persistent, managed,
# HEADED browser you log into ONCE — it is NOT your real Chrome). We mirror that:
#  - DEFAULT: a dedicated persistent profile ("--profile <dir>"), HEADED. Cookies
#    persist across runs; log in once via web_login. The managed browser stays
#    WARM and is RECONNECTED on the next run (no fresh window each time) — exactly
#    like OpenClaw, which keeps its managed browser alive between tasks.
#  - BROWSER_HEADLESS=1 -> hidden window (still the same persistent profile; good
#    for quiet/batch/server runs once you're logged in).
#  - OPT-IN session modes (these are OpenClaw's "user" profile, not its default):
#      BROWSER_ATTACH=1   -> attach to your already-running real Chrome
#                            ("--auto-connect"; needs Chrome started with
#                            --remote-debugging-port) — uses your real logins.
#      BROWSER_CDP=<port> -> attach to a Chrome on that specific CDP port.
#  - BROWSER_FRESH=1         -> force a clean restart instead of reconnecting.
#  - BROWSER_CLOSE_ON_DONE=1 -> close the browser when a task ends (default: warm).
def _truthy(name):
    return os.getenv(name, "").lower() in ("1", "true", "yes")


# Hard cap on snapshot text handed back to the model. Content snapshots of heavy
# sites (LinkedIn results + detail panel + footer) can run 40k+ chars; unbounded,
# they bloat context and can make the provider return an empty response. OpenClaw
# caps AI snapshots at 40k by default — we trim a bit tighter since our content
# tree is more verbose. The signal (result list / job description) sits near the
# top; the tail is mostly footer/"related" noise, so truncating the tail is safe.
SNAP_MAX = int(os.getenv("BROWSER_SNAPSHOT_MAX", "28000"))

PROFILE = os.getenv("BROWSER_PROFILE") or str(Path.home() / ".pc_agent_browser_profile")
_CDP = os.getenv("BROWSER_CDP", "").strip()
_ATTACH = bool(_CDP) or _truthy("BROWSER_ATTACH")

if _CDP:
    _GLOBAL = ["--cdp", _CDP]
elif _truthy("BROWSER_ATTACH"):
    _GLOBAL = ["--auto-connect"]
else:
    _GLOBAL = ["--profile", PROFILE]
if not _truthy("BROWSER_HEADLESS"):              # headed by default (OpenClaw-identical)
    _GLOBAL.append("--headed")


def _run(args, timeout=_T):
    # NOTE: launch flags (_GLOBAL: --profile/--headed/--cdp) are applied ONLY by
    # open_url when it starts the daemon. Passing them to read/act commands makes
    # agent-browser print "--profile/--headed ignored: daemon already running",
    # and that warning leaks into extracted text — so they're omitted here.
    try:
        p = subprocess.run([CLI, *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return ((p.stdout or "") + (p.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        return "[agent-browser timed out]"
    except FileNotFoundError:
        return "[agent-browser not installed — run: npm i -g agent-browser && agent-browser install]"
    except Exception as e:                       # noqa: BLE001
        return f"[agent-browser error: {e}]"


def snapshot(interactive=False, urls=True, timeout=30):
    """Accessibility-tree snapshot of the current page.

    Default = CONTENT snapshot (OpenClaw's default): includes heading/article/
    list/paragraph TEXT *and* the interactive @refs + link URLs — so the agent can
    READ the page (job descriptions, article bodies, result lists) AND act on it.
    `interactive=True` requests the slimmer interactive-only tree (buttons/links/
    inputs, no body text) — useful only when you just need clickable refs, e.g. a
    big form. Reading/extraction must use the default (content) snapshot, because
    `-i` strips all the readable text."""
    args = ["snapshot"]
    if interactive:
        args.append("-i")
    if urls:
        args.append("-u")
    out = _run(args + ["--json"], timeout=timeout)
    try:
        d = json.loads(out)
        data = d.get("data", {})
        text = data.get("snapshot", "")
        result = (f"URL: {data.get('origin', '')}\n{text}").strip()
    except Exception:
        result = out
    if len(result) > SNAP_MAX:
        result = (result[:SNAP_MAX]
                  + "\n…[snapshot truncated — scroll down or open a specific "
                    "result/link for the rest]")
    return result or "[empty page]"


def open_url(url):
    if "://" not in url:
        url = "https://" + url
    host = urllib.parse.urlparse(url).netloc
    # OpenClaw parity: the managed browser stays WARM across runs. If it's already
    # running (this profile's daemon from a previous task), `open` just RECONNECTS
    # and navigates it — no fresh window, still logged in. If nothing's running yet,
    # `open` LAUNCHES it (headed, on our profile). We do NOT kill/relaunch each run.
    # Escape hatch: BROWSER_FRESH=1 forces a clean restart (e.g. after changing
    # BROWSER_* modes, which would otherwise leave a daemon on the old settings).
    if _truthy("BROWSER_FRESH") and not _ATTACH:
        try:
            subprocess.run([CLI, "close", "--all"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=15)
        except Exception:
            pass
    # Launch detached so a cold daemon doesn't block us on a captured pipe.
    try:
        subprocess.Popen([CLI, *_GLOBAL, "open", url], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, **_DETACH)
    except FileNotFoundError:
        return "[agent-browser not installed — run: npm i -g agent-browser && agent-browser install]"
    # HARD wall-clock cap so a slow/hostile site can never hang us to the watchdog.
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        time.sleep(0.5)
        u = _run(["get", "url"], timeout=6)
        if host and host in u:
            break
        if u.startswith("[") and "timed out" not in u:
            break
    snap = snapshot(timeout=25)
    if is_login_wall(snap):
        return (f"[NOT LOGGED IN] {host} is showing a sign-in wall — the content "
                f"is gated behind login. Call web_login with this site's login URL "
                f"so the user can sign in, then retry web_open.\n\n{snap}")
    return snap


_WALL_HINTS = ("sign in to view", "sign in to see", "sign in to continue",
               "join now to", "please sign in", "log in to continue",
               "you must be logged in", "sign in to access")


def is_login_wall(text):
    t = (text or "").lower()
    return any(h in t for h in _WALL_HINTS)


def login(url=""):
    """Open a VISIBLE (headed) browser on the persistent profile so the user can
    log in. Cookies persist in the profile; later headless runs are authenticated."""
    target = url or "about:blank"
    if target != "about:blank" and "://" not in target:
        target = "https://" + target
    try:                                         # restart daemon headed with the profile
        subprocess.run([CLI, "close", "--all"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=20)
    except Exception:
        pass
    try:
        subprocess.Popen([CLI, "--profile", PROFILE, "--headed", "open", target],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **_DETACH)
    except FileNotFoundError:
        return "[agent-browser not installed — run: npm i -g agent-browser && agent-browser install]"
    return f"Opened a visible browser at {target} using your saved profile."


def click(ref):
    r = _run(["click", ref])
    return (r + "\n\n" + snapshot()).strip()     # refs go stale after a click -> re-snapshot


def fill(ref, text):
    return _run(["fill", ref, text]) or "[filled]"


def press(key):
    r = _run(["press", key])
    return (r + "\n\n" + snapshot()).strip()


def get_text(ref):
    return _run(["get", "text", ref])


def scroll(direction, pixels=None):
    args = ["scroll", direction] + ([str(pixels)] if pixels else [])
    return (_run(args) + "\n\n" + snapshot()).strip()


def close():
    if _ATTACH:                                  # never close the user's own Chrome
        return "[detached — left your Chrome open]"
    # OpenClaw parity: it keeps the managed browser warm between tasks rather than
    # tearing it down, so the next run reconnects instantly (no fresh window, still
    # logged in). So by default we LEAVE it running. Set BROWSER_CLOSE_ON_DONE=1 to
    # actually close it when a task finishes.
    if not _truthy("BROWSER_CLOSE_ON_DONE"):
        return "[left browser warm for the next run — set BROWSER_CLOSE_ON_DONE=1 to close it]"
    _run(["close"], timeout=20)
    return "[browser closed]"

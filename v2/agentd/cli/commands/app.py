"""`agentd app` — open an APP AGENT's own UI (served by the daemon at /apps/<id>/).

An app agent ships a `ui/` + an `[app]` section in its agent.toml (docs/PROTOCOL.md §9);
the daemon serves that UI on the gateway port, same origin as the WebSocket. This command
is the LOCAL opener and a product ENTRY POINT: `open`/`url` START the daemon when none is
running (same as bare `agentd`), resolve its host/port/token from the rendezvous file, and
mint the tokenized, agent-scoped URL — the browser does the rest. `list` stays passive
(a read-only query never boots a daemon as a side effect). On a hosted deployment the
opener is the web login instead; the app itself is identical.
"""

from __future__ import annotations

import argparse


def register(subparsers: argparse._SubParsersAction) -> None:
    app = subparsers.add_parser("app", help="agent apps: list / url / open an agent's own UI")
    app.set_defaults(func=run_list)
    sub = app.add_subparsers(dest="app_command")

    ls = sub.add_parser("list", help="list installed app agents")
    ls.set_defaults(func=run_list)

    url = sub.add_parser("url", help="print the tokenized URL for an agent's app")
    url.add_argument("agent_id")
    url.set_defaults(func=run_url)

    op = sub.add_parser(
        "open",
        help="open an agent's app (honors the agent's declared [app] mode: browser | window)",
    )
    op.add_argument("agent_id")
    op.add_argument(
        "--window",
        action="store_true",
        help="force a chromeless app window (its own window, no tabs/address bar); "
        "falls back to the default browser when no Chromium-family browser is found",
    )
    op.add_argument(
        "--browser",
        action="store_true",
        help='force a normal browser tab even when the agent declares mode = "window"',
    )
    op.set_defaults(func=run_open)


def _app_agents() -> list[dict]:
    from agentd.cli import rpc

    agents = rpc.call("agents.list", timeout=15).get("agents", [])
    return [a for a in agents if a.get("app")]


def _mint_url(agent_id: str) -> tuple[str, dict]:
    """The full local app URL: daemon origin + /apps/<id>/ + this machine's token + scope.
    Opening an app is a product ENTRY POINT (like bare `agentd`), so a daemon that isn't
    running yet is started here — double-clicking an app must just work. Returns
    ``(url, app-descriptor)`` — the descriptor carries the author's declared `mode`."""
    from agentd import lifecycle
    from agentd.cli import rpc

    info, spawned = lifecycle.ensure_running()
    if spawned:
        print(f"agentd: started (pid {info.pid}, {info.ws_url})")
    apps = {a["id"]: a for a in _app_agents()}
    agent = apps.get(agent_id)
    if agent is None:
        known = ", ".join(sorted(apps)) or "none installed"
        raise rpc.RpcError(f"'{agent_id}' has no app UI (app agents: {known})")
    url = f"http://{info.host}:{info.port}{agent['app']['url']}"
    query = f"scope=agent:{agent_id}"
    if info.token:
        query = f"token={info.token}&{query}"
    return f"{url}?{query}", dict(agent["app"])


def run_list(_args: argparse.Namespace) -> int:
    from agentd.cli import rpc

    try:
        apps = _app_agents()
    except (rpc.DaemonNotRunning, rpc.RpcError) as e:
        print(f"error: {e}")
        return 1
    if not apps:
        print("no app agents installed (an app agent ships ui/ + [app] in its agent.toml)")
        return 0
    print("app agents:")
    for a in apps:
        mode = a["app"].get("mode") or "browser"
        print(f"  {a['id']:<20} {a['app']['title']} ({mode})  ->  agentd app open {a['id']}")
    return 0


def run_url(args: argparse.Namespace) -> int:
    from agentd.cli import rpc

    try:
        url, _app = _mint_url(args.agent_id)
        print(url)
        return 0
    except (rpc.DaemonNotRunning, rpc.RpcError) as e:
        print(f"error: {e}")
        return 1


def _find_chromium() -> str:
    """An installed Chromium-family browser for ``--app=`` chromeless windows.
    AGENTD_APP_BROWSER (a path or exe name) overrides discovery; then PATH; then the
    well-known Windows install locations (Edge ships with Windows but isn't on PATH).
    Returns '' when none is found — the caller falls back to a normal browser tab."""
    import os
    import shutil

    override = (os.environ.get("AGENTD_APP_BROWSER") or "").strip()
    if override:
        return shutil.which(override) or (override if os.path.isfile(override) else "")
    for name in ("msedge", "chrome", "google-chrome", "chromium", "chromium-browser", "brave"):
        path = shutil.which(name)
        if path:
            return path
    for candidate in (
        r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
        r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
        r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
    ):
        expanded = os.path.expandvars(candidate)
        if "%" not in expanded and os.path.isfile(expanded):
            return expanded
    return ""


def _launch_app_window(url: str) -> bool:
    """Open ``url`` as a chromeless app window (``--app=``) — the closest thing to
    'running the agent as its own program' without shipping a shell exe."""
    import subprocess

    browser = _find_chromium()
    if not browser:
        return False
    subprocess.Popen([browser, f"--app={url}"])  # browser detaches itself; CLI may exit
    return True


def run_open(args: argparse.Namespace) -> int:
    import webbrowser

    from agentd.cli import rpc

    try:
        url, app = _mint_url(args.agent_id)
    except (rpc.DaemonNotRunning, rpc.RpcError) as e:
        print(f"error: {e}")
        return 1
    # presentation: explicit flags win; otherwise the AGENT's declared [app] mode decides
    want_window = bool(getattr(args, "window", False)) or (
        not getattr(args, "browser", False) and (app.get("mode") or "browser") == "window"
    )
    if want_window and _launch_app_window(url):
        print(f"opened {args.agent_id} (app window) -> {url.split('?')[0]}")
        return 0
    webbrowser.open(url)
    print(f"opened {args.agent_id} -> {url.split('?')[0]}")
    return 0

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

    op = sub.add_parser("open", help="open an agent's app in the default browser")
    op.add_argument("agent_id")
    op.set_defaults(func=run_open)


def _app_agents() -> list[dict]:
    from agentd.cli import rpc

    agents = rpc.call("agents.list", timeout=15).get("agents", [])
    return [a for a in agents if a.get("app")]


def _mint_url(agent_id: str) -> str:
    """The full local app URL: daemon origin + /apps/<id>/ + this machine's token + scope.
    Opening an app is a product ENTRY POINT (like bare `agentd`), so a daemon that isn't
    running yet is started here — double-clicking an app must just work."""
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
    return f"{url}?{query}"


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
        print(f"  {a['id']:<20} {a['app']['title']}  ->  agentd app open {a['id']}")
    return 0


def run_url(args: argparse.Namespace) -> int:
    from agentd.cli import rpc

    try:
        print(_mint_url(args.agent_id))
        return 0
    except (rpc.DaemonNotRunning, rpc.RpcError) as e:
        print(f"error: {e}")
        return 1


def run_open(args: argparse.Namespace) -> int:
    import webbrowser

    from agentd.cli import rpc

    try:
        url = _mint_url(args.agent_id)
    except (rpc.DaemonNotRunning, rpc.RpcError) as e:
        print(f"error: {e}")
        return 1
    webbrowser.open(url)
    print(f"opened {args.agent_id} -> {url.split('?')[0]}")
    return 0

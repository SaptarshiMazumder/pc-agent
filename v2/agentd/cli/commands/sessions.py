"""`agentd sessions` — saved conversations from the terminal: list / delete / rename.

Same backend the desktop sidebar uses (sessions.* RPCs). Daemon running -> RPC
(deletes broadcast sessions.changed so every open client updates live). No daemon ->
operate on the same on-disk stores directly; nothing is client-specific."""

from __future__ import annotations

import argparse

from agentd.clients.timefmt import whatsapp_when


def register(subparsers: argparse._SubParsersAction) -> None:
    sessions = subparsers.add_parser(
        "sessions", help="saved conversations: list / delete / rename")
    sessions.add_argument("--agent", default="", help="agent id (default: the default agent)")
    sessions.set_defaults(func=run_list)
    sub = sessions.add_subparsers(dest="sessions_command")

    ls = sub.add_parser("list", help="list saved conversations (newest first)")
    ls.add_argument("--agent", default="")
    ls.set_defaults(func=run_list)

    rm = sub.add_parser("delete", help="delete a conversation — permanent")
    rm.add_argument("session", help="session id (see `agentd sessions`)")
    rm.add_argument("--agent", default="")
    rm.set_defaults(func=run_delete)

    mv = sub.add_parser("rename", help="set a conversation's title")
    mv.add_argument("session")
    mv.add_argument("title")
    mv.add_argument("--agent", default="")
    mv.set_defaults(func=run_rename)


def _offline_state_dir(agent: str):
    """No daemon: resolve the agent's session partition with the SAME registry the
    daemon composes — identical layout, zero duplicated conventions."""
    from agentd.config import load_config
    from agentd.infrastructure.agents.file_registry import FileAgentRegistry

    registry = FileAgentRegistry(load_config())
    agent_id = (agent or "").strip() or "main"
    try:
        return agent_id, registry.get(agent_id).state_dir
    except KeyError:
        return "main", registry.get("main").state_dir


def run_list(args: argparse.Namespace) -> int:
    from agentd.cli import rpc

    agent = getattr(args, "agent", "")
    try:
        payload = rpc.call("sessions.list", {"agentId": agent} if agent else {}, timeout=15)
        rows, agent_id = payload.get("sessions", []), payload.get("agentId", agent or "main")
    except rpc.DaemonNotRunning:
        from agentd.infrastructure.memory.local_store import list_sessions
        agent_id, state_dir = _offline_state_dir(agent)
        rows = list_sessions(state_dir)
    except rpc.RpcError as e:
        print(f"error: {e}")
        return 1
    if not rows:
        print(f"no saved conversations for agent '{agent_id}'")
        return 0
    print(f"conversations — agent {agent_id}:")
    for s in rows:
        title = s.get("title") or s.get("sessionId", "?")
        when = whatsapp_when(s.get("modified") or 0)
        proj = f"  [project {s['projectId']}]" if s.get("projectId") else ""
        print(f"  {s.get('sessionId', '?'):<22} {when:>10}  {s.get('messages', 0):>3} msgs  "
              f"{title[:48]}{proj}")
    print("\ndelete: agentd sessions delete <id> · rename: agentd sessions rename <id> <title>")
    return 0


def run_delete(args: argparse.Namespace) -> int:
    from agentd.cli import rpc

    params = {"sessionKey": args.session}
    if args.agent:
        params["agentId"] = args.agent
    try:
        result = rpc.call("sessions.delete", params, timeout=15)
        if not result.get("ok"):
            print(f"delete failed: {result.get('error', '?')}")
            return 1
        print(f"deleted {args.session}" if result.get("deleted")
              else f"no such conversation: {args.session}")
        return 0 if result.get("deleted") else 1
    except rpc.DaemonNotRunning:
        from agentd.infrastructure.memory.local_store import delete_session
        _, state_dir = _offline_state_dir(args.agent)
        if delete_session(state_dir, args.session):
            print(f"deleted {args.session}")
            return 0
        print(f"no such conversation: {args.session}")
        return 1
    except rpc.RpcError as e:
        print(f"delete failed: {e}")
        return 1


def run_rename(args: argparse.Namespace) -> int:
    from agentd.cli import rpc

    params = {"sessionKey": args.session, "title": args.title}
    if args.agent:
        params["agentId"] = args.agent
    try:
        result = rpc.call("sessions.rename", params, timeout=15)
        if not result.get("ok"):
            print(f"rename failed: {result.get('error', '?')}")
            return 1
    except rpc.DaemonNotRunning:
        from agentd.infrastructure.memory.local_store import write_session_meta
        _, state_dir = _offline_state_dir(args.agent)
        title = args.title.strip()[:80]
        write_session_meta(state_dir, args.session, title=title, manual=bool(title))
    except rpc.RpcError as e:
        print(f"rename failed: {e}")
        return 1
    print(f"renamed {args.session} -> {args.title.strip()[:80]!r}")
    return 0

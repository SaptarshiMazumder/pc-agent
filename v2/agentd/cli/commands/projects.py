"""`agentd projects` — ChatGPT-style projects (folders of conversations).

Projects are SERVER data: one global list every client shares. Chat inside one with
`agentd --project <id>` (or the desktop's per-project “+ chat”); a standalone chat
stays exactly what it is today. Daemon running -> RPC; no daemon -> the same
on-disk store directly."""

from __future__ import annotations

import argparse

from agentd.clients.timefmt import whatsapp_when


def register(subparsers: argparse._SubParsersAction) -> None:
    projects = subparsers.add_parser(
        "projects", help="projects (folders of chats): list / create / rename / delete")
    projects.set_defaults(func=run_list)
    sub = projects.add_subparsers(dest="projects_command")

    ls = sub.add_parser("list", help="list projects")
    ls.set_defaults(func=run_list)

    mk = sub.add_parser("create", help="create a project")
    mk.add_argument("name")
    mk.set_defaults(func=run_create)

    mv = sub.add_parser("rename", help="rename a project")
    mv.add_argument("id")
    mv.add_argument("name")
    mv.set_defaults(func=run_rename)

    rm = sub.add_parser("delete", help="delete a project (chats become standalone)")
    rm.add_argument("id")
    rm.add_argument("--delete-chats", action="store_true",
                    help="ALSO delete every conversation in the project — permanent")
    rm.set_defaults(func=run_delete)


def run_list(_args: argparse.Namespace) -> int:
    from agentd.cli import rpc

    try:
        rows = rpc.call("projects.list", timeout=15).get("projects", [])
    except rpc.DaemonNotRunning:
        from agentd.config import load_config
        from agentd.infrastructure.memory import projects_store
        rows = projects_store.list_projects(load_config().state_dir)
    except rpc.RpcError as e:
        print(f"error: {e}")
        return 1
    if not rows:
        print("no projects yet — create one: agentd projects create <name>")
        return 0
    print("projects:")
    for p in rows:
        when = whatsapp_when(p.get("createdAt") or 0)
        print(f"  {p['id']:<16} {when:>10}  {p['name']}")
    print("\nchat inside one: agentd --project <id>   ·   delete: agentd projects delete <id>")
    return 0


def run_create(args: argparse.Namespace) -> int:
    from agentd.cli import rpc

    try:
        project = rpc.call("projects.create", {"name": args.name}, timeout=15).get("project", {})
    except rpc.DaemonNotRunning:
        from agentd.config import load_config
        from agentd.infrastructure.memory import projects_store
        try:
            project = projects_store.create_project(load_config().state_dir, args.name)
        except ValueError as e:
            print(f"create failed: {e}")
            return 1
    except rpc.RpcError as e:
        print(f"create failed: {e}")
        return 1
    print(f"created {project.get('id')}  {project.get('name')}")
    print(f"chat inside it: agentd --project {project.get('id')}")
    return 0


def run_rename(args: argparse.Namespace) -> int:
    from agentd.cli import rpc

    try:
        ok = rpc.call("projects.rename", {"id": args.id, "name": args.name},
                      timeout=15).get("ok")
    except rpc.DaemonNotRunning:
        from agentd.config import load_config
        from agentd.infrastructure.memory import projects_store
        ok = projects_store.rename_project(load_config().state_dir, args.id, args.name)
    except rpc.RpcError as e:
        print(f"rename failed: {e}")
        return 1
    print(f"renamed {args.id} -> {args.name!r}" if ok else f"no such project: {args.id}")
    return 0 if ok else 1


def run_delete(args: argparse.Namespace) -> int:
    from agentd.cli import rpc

    try:
        result = rpc.call("projects.delete",
                          {"id": args.id, "deleteSessions": args.delete_chats}, timeout=60)
        ok = result.get("ok")
        tail = (f" (+ {result.get('sessionsDeleted', 0)} chats deleted)"
                if args.delete_chats else " (its chats are standalone now)")
    except rpc.DaemonNotRunning:
        from agentd.config import load_config
        from agentd.infrastructure.agents.file_registry import FileAgentRegistry
        from agentd.infrastructure.memory import projects_store
        from agentd.infrastructure.memory.local_store import (
            delete_session,
            sessions_in_project,
            write_session_meta,
        )
        config = load_config()
        ok = projects_store.delete_project(config.state_dir, args.id)
        registry = FileAgentRegistry(config)
        deleted = 0
        state_dirs = {str(config.state_dir): config.state_dir}
        for aid in registry.list_ids():
            sd = registry.get(aid).state_dir
            state_dirs[str(sd)] = sd
        for sd in state_dirs.values():
            for sid in sessions_in_project(sd, args.id):
                if args.delete_chats:
                    delete_session(sd, sid)
                    deleted += 1
                else:
                    write_session_meta(sd, sid, projectId="")
        tail = (f" (+ {deleted} chats deleted)" if args.delete_chats
                else " (its chats are standalone now)")
    except rpc.RpcError as e:
        print(f"delete failed: {e}")
        return 1
    print(f"deleted {args.id}{tail}" if ok else f"no such project: {args.id}")
    return 0 if ok else 1

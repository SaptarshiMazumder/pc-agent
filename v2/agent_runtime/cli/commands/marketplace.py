"""`agentd install / uninstall / bundles` — the marketplace from the terminal (M4).

Same flow the desktop store uses. Daemon running -> RPC (install hot-reloads live,
progress streams back). No daemon -> the service runs in-process (offline install;
effective on next start). One implementation, two transports."""

from __future__ import annotations

import argparse
import asyncio


def register(subparsers: argparse._SubParsersAction) -> None:
    install = subparsers.add_parser(
        "install", help="install an agent bundle (registry id, .agentpkg file, or url)"
    )
    install.add_argument("bundle", help="bundle id in the registry, or a path to a .agentpkg")
    install.add_argument(
        "--registry", default="", help="registry override (dir, file://, https://)"
    )
    install.set_defaults(func=run_install)

    uninstall = subparsers.add_parser("uninstall", help="remove an installed bundle")
    uninstall.add_argument("bundle", help="installed bundle id")
    uninstall.add_argument(
        "--purge",
        action="store_true",
        help="ALSO delete the agent's workspace and state (default keeps them)",
    )
    uninstall.set_defaults(func=run_uninstall)

    bundles = subparsers.add_parser("bundles", help="installed bundles + what the registry offers")
    bundles.add_argument("--registry", default="", help="registry override")
    bundles.set_defaults(func=run_bundles)


def _print_progress(event: str, payload: dict) -> None:
    if event == "marketplace.progress":
        print(f"  [{payload.get('step', '?'):<12}] {payload.get('message', '')}")


def run_install(args: argparse.Namespace) -> int:
    from pathlib import Path

    from agent_runtime.cli import rpc

    is_file = args.bundle.endswith(".agentpkg") or Path(args.bundle).is_file()
    params = {"file": args.bundle} if is_file else {"id": args.bundle}
    if not args.registry:
        try:
            result = rpc.call("marketplace.install", params, timeout=1800, on_event=_print_progress)
            return _report_install(result)
        except rpc.DaemonNotRunning:
            pass
        except rpc.RpcError as e:
            print(f"install failed: {e}")
            return 1
    # offline (or explicit --registry): run the service in-process
    result = _local_service(args.registry, verbose=True)
    try:
        outcome = asyncio.run(result.install(**{("file" if is_file else "bundle_id"): args.bundle}))
    except Exception as e:  # noqa: BLE001 — BundleError et al. are user-renderable
        print(f"install failed: {e}")
        return 1
    print("note: no daemon was running — the bundle is installed and will be live on next start")
    return _report_install(outcome)


def _report_install(result: dict) -> int:
    print(
        f"installed {result.get('id')} {result.get('version')}"
        + (
            f"  (plugins: {', '.join(result.get('plugins') or [])})"
            if result.get("plugins")
            else ""
        )
    )
    return 0


def run_uninstall(args: argparse.Namespace) -> int:
    from agent_runtime.cli import rpc

    try:
        rpc.call("marketplace.uninstall", {"id": args.bundle, "purge": args.purge}, timeout=300)
        print(f"uninstalled {args.bundle}")
        return 0
    except rpc.DaemonNotRunning:
        pass
    except rpc.RpcError as e:
        print(f"uninstall failed: {e}")
        return 1
    service = _local_service("", verbose=True)
    try:
        asyncio.run(service.uninstall(args.bundle, purge_state=args.purge))
    except Exception as e:  # noqa: BLE001
        print(f"uninstall failed: {e}")
        return 1
    print(f"uninstalled {args.bundle}")
    return 0


def run_bundles(args: argparse.Namespace) -> int:
    from agent_runtime.cli import rpc

    try:
        installed = rpc.call("marketplace.installed", timeout=15)
        catalog = rpc.call("marketplace.catalog", timeout=60)
    except rpc.DaemonNotRunning:
        service = _local_service(args.registry, verbose=False)
        installed = service.installed()
        catalog = asyncio.run(service.catalog())
    except rpc.RpcError as e:
        print(f"error: {e}")
        return 1
    print("installed:")
    rows = installed.get("bundles", [])
    for bundle in rows:
        print(f"  {bundle['id']:<24} {bundle['version']:<10} {bundle.get('installedAt', '')}")
    if not rows:
        print("  (none)")
    print("\nregistry:")
    if catalog.get("error"):
        print(f"  {catalog['error']}")
        return 0
    for bundle in catalog.get("bundles", []):
        badges = []
        if bundle.get("installed"):
            badges.append("installed " + bundle.get("installedVersion", ""))
        if bundle.get("updateAvailable"):
            badges.append("UPDATE")
        if not bundle.get("compatible", True):
            badges.append("needs newer agentd")
        badge = f"  [{', '.join(badges)}]" if badges else ""
        print(
            f"  {bundle['id']:<24} {bundle['version']:<10} {bundle.get('description', '')[:60]}{badge}"
        )
    return 0


def _local_service(registry_override: str, verbose: bool):
    from agent_runtime.config import load_config
    from agent_runtime.infrastructure.marketplace import build_marketplace_service

    on_event = (
        (lambda p: print(f"  [{p.get('step', '?'):<12}] {p.get('message', '')}"))
        if verbose
        else None
    )
    return build_marketplace_service(
        load_config(), on_event=on_event, registry_url=registry_override
    )

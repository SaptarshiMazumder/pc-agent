"""`agentd product ...` — ship ONE agent as its own desktop app.

    agentd product payload <agent>     the ~50 KB an existing engine can run with --app-dir
    agentd product build   <agent>     that, plus the installer a stranger double-clicks
    agentd product engine              what engine a stub would install, and where that came from

WHAT MAKES THIS DIFFERENT FROM THE OLD BUILD. Producing a per-agent installer used to require the
desktop client's toolchain: node, electron-builder, and a prebuilt ~250 MB CPython tree, driven by
two Node scripts and a bash wrapper. The result was a 250 MB installer per agent. Here the engine
is shared and already signed, so this command needs none of that — only makensis for the stub, and
not even that for a payload.

    agentd product build weather
      -> dist/products/weather/payload/            (distribution.toml + bundles/ + icon)
      -> dist/products/weather/weather-1.2.0-setup.exe

`--pkg` builds from a published .agentpkg instead of a local agent directory, which is the shape a
publish service sees. Nothing else about the command changes — same service, same output.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def register(subparsers: argparse._SubParsersAction) -> None:
    product = subparsers.add_parser(
        "product", help="ship one agent as its own app (payload + installer)"
    )
    sub = product.add_subparsers(dest="product_cmd", required=True)

    def common(parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "agent",
            nargs="?",
            default="",
            help="agent id under the agents dir (omit when using --pkg)",
        )
        parser.add_argument(
            "--pkg",
            default="",
            help="build from this .agentpkg instead of a local agent directory",
        )
        parser.add_argument("--out", default="", help="output directory (default: dist/products/<id>)")
        parser.add_argument("--name", default="", help="override the product's display name")
        parser.add_argument("--version", default="", help="override the product's version")
        parser.add_argument(
            "--icon",
            default="",
            help="icon path relative to the agent dir, tried before the agent's own declaration",
        )
        parser.add_argument(
            "--agents-dir", default="", help="where agents live (default: this install's agents dir)"
        )
        # HOSTED vs BYOK. Normally inherited from THIS install's distribution profile, so a hosted
        # build produces hosted products and a plain checkout produces BYOK ones. These override
        # it — used by the desktop build, which knows the repo layout and reads the core flavor
        # itself. That knowledge stays in the repo-local script; nothing here guesses a path.
        parser.add_argument("--accounts-url", default="", help="sign-in service for this product")
        parser.add_argument(
            "--model-proxy-url", default="", help="hosted model proxy for this product"
        )

    payload = sub.add_parser("payload", help="write the payload only (no installer)")
    common(payload)
    payload.set_defaults(func=run_payload)

    build = sub.add_parser("build", help="payload + the per-agent installer")
    common(build)
    build.add_argument(
        "--platform",
        default="",
        help="target platform for the installer (default: win — the only one with a builder)",
    )
    build.set_defaults(func=run_build)

    engine = sub.add_parser("engine", help="show which engine a stub would install")
    engine.add_argument("--platform", default="", help="default: win")
    engine.set_defaults(func=run_engine)


# ────────────────────────────── helpers ──────────────────────────────


def _source(args, config):
    """-> (ProductSource, agent id for output naming). Exactly one of the two shapes."""
    from agent_runtime.application.interfaces.product import ProductSource

    if args.pkg:
        package = Path(args.pkg).expanduser().resolve()
        if not package.is_file():
            raise ValueError(f"no such package: {package}")
        return ProductSource(package=package), package.stem
    agent_id = (args.agent or "").strip()
    if not agent_id:
        raise ValueError("give an agent id, or --pkg <file> to build from a published package")
    agents_dir = Path(args.agents_dir).expanduser() if args.agents_dir else Path(config.agents_dir)
    agent_dir = (agents_dir / agent_id).resolve()
    if not (agent_dir / "agent.toml").is_file():
        raise ValueError(f"no agent '{agent_id}' in {agents_dir} (looked for agent.toml)")
    return ProductSource(agent_dir=agent_dir), agent_id


def _overrides(args):
    from agent_runtime.domain.product import PlatformEndpoints, ProductOverrides

    accounts = getattr(args, "accounts_url", "") or ""
    proxy = getattr(args, "model_proxy_url", "") or ""
    # BOTH or NEITHER. One without the other builds a product that prompts for sign-in and then
    # fails every model call — worse than an honestly BYOK build.
    platform = PlatformEndpoints(accounts, proxy) if (accounts and proxy) else None
    return ProductOverrides(
        name=args.name,
        version=args.version,
        icon=getattr(args, "icon", "") or "",
        platform=platform,
    )


def _out_dir(args, label: str, config) -> Path:
    if args.out:
        return Path(args.out).expanduser().resolve()
    # Under dist/, which is build output — never inside the agent's own directory, because the
    # packer excludes agents/<id>/clients/ but not an arbitrary new folder, and a product nested
    # inside its own source would end up inside the next .agentpkg.
    return (Path(config.state_dir) / "dist" / "products" / label).resolve()


def _report(build, out_dir: Path) -> None:
    payload = build.payload
    print(f"product: {build.spec.name} v{build.spec.version}  (agent: {build.spec.agent_id})")
    print(f"payload: {payload.dir}  ({payload.size:,} bytes, {len(payload.files)} files)")
    for rel in payload.files:
        print(f"    {rel}")
    if build.engine:
        print(f"engine:  {build.engine.version or '(unversioned)'}  {build.engine.url}")
    if build.stub:
        print(f"\ninstaller: {build.stub}  ({build.stub.stat().st_size:,} bytes)")
        print("Anyone can run this — it installs the engine if their machine has none.")
    else:
        print(f"\nno installer (payload is in {out_dir}). Run it on a machine that has the engine:")
        print(f"    <Engine>.exe --app-dir \"{payload.dir}\"")
    for warning in build.warnings:
        print(f"\nNOTE: {warning}")


# ────────────────────────────── commands ──────────────────────────────


def run_payload(args: argparse.Namespace) -> int:
    from agent_runtime.config import load_config
    from agent_runtime.domain.product import ProductError
    from agent_runtime.infrastructure.products.factory import build_product_service

    config = load_config()
    try:
        source, label = _source(args, config)
        out_dir = _out_dir(args, label, config)
        service = build_product_service(config)
        build = service.payload(source, out_dir / "payload", _overrides(args))
    except (ProductError, ValueError) as e:
        print(e)
        return 1
    _report(build, out_dir)
    return 0


def run_build(args: argparse.Namespace) -> int:
    from agent_runtime.config import load_config
    from agent_runtime.domain.product import ProductError
    from agent_runtime.infrastructure.products.factory import DEFAULT_TARGET, build_product_service

    config = load_config()
    try:
        source, label = _source(args, config)
        out_dir = _out_dir(args, label, config)
        service = build_product_service(config, args.platform or DEFAULT_TARGET)
        build = service.build(source, out_dir / "payload", out_dir, _overrides(args))
    except (ProductError, ValueError) as e:
        print(e)
        return 1
    _report(build, out_dir)
    # A payload with no installer is a SUCCESS with a warning, not a failure: it is the part that
    # carries the agent, and the two hosts that cannot build installers (a Linux service, a plain
    # checkout) both legitimately want it.
    return 0


def run_engine(args: argparse.Namespace) -> int:
    from agent_runtime.config import load_config
    from agent_runtime.infrastructure.products.factory import (
        DEFAULT_TARGET,
        engine_catalog,
        product_defaults,
    )

    config = load_config()
    platform = (args.platform or DEFAULT_TARGET).strip().lower()
    ref = engine_catalog(config).resolve(platform)
    if ref is None:
        print(f"no {platform} engine known to this install.")
        print("Sources checked, in order:")
        print("  1. config engine_installer_url + engine_installer_sha256 (AGENTD_ENGINE_URL/…)")
        print(f"  2. the `engine` block of {config.registry_url or '(no registry configured)'}")
        print("\nWithout one, `agentd product build` writes a payload but no installer.")
        return 1
    print(f"platform: {ref.platform}")
    print(f"version:  {ref.version or '(unversioned)'}")
    print(f"url:      {ref.url}")
    print(f"sha256:   {ref.sha256 or '(MISSING — no stub will be built without it)'}")
    minimum = product_defaults(config).engine_min_version
    print(f"payloads built here require: {minimum or 'any engine (additive-only contract)'}")
    return 0 if ref.usable else 1

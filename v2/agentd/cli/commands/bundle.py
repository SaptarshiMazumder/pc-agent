"""`agentd bundle ...` — PUBLISHER tooling (us, and later third parties):

  pack    an agents/<id>/ directory (+ vendored plugins) -> <id>-<ver>.agentpkg
  index   a directory of .agentpkg files -> index.json  (= a complete registry)
  serve   that directory over http://localhost — a real local marketplace
  keygen  an ed25519 keypair for signing indexes (M7)

The `pack` defaults come from an optional `bundle.toml` inside the agent dir, so a
bundle's identity lives WITH the agent (flags override)."""

from __future__ import annotations

import argparse
import tomllib
from pathlib import Path

from agentd.domain.bundle import BundleManifest, PluginDep, parse_bundle_manifest


def register(subparsers: argparse._SubParsersAction) -> None:
    bundle = subparsers.add_parser("bundle", help="publisher tools: pack / index / serve / keygen")
    sub = bundle.add_subparsers(dest="bundle_command", required=True)

    pack = sub.add_parser("pack", help="agents/<id>/ dir -> .agentpkg")
    pack.add_argument("agent_dir", help="the agent directory to pack")
    pack.add_argument("--out", default="dist", help="output directory (default ./dist)")
    pack.add_argument(
        "--version", default="", help="bundle version (default from bundle.toml or 1.0.0)"
    )
    pack.add_argument(
        "--vendor-plugins",
        default="",
        help="comma-separated plugin ids to VENDOR into the zip (copied from this "
        "install's plugin roots)",
    )
    pack.add_argument(
        "--builtin-plugins",
        default="",
        help="comma-separated plugin ids the bundle REQUIRES as built-ins",
    )
    pack.set_defaults(func=run_pack)

    index = sub.add_parser("index", help="directory of .agentpkg -> index.json (a registry)")
    index.add_argument("directory")
    index.add_argument("--name", default="", help="registry display name")
    index.add_argument("--publisher", default="", help="publisher name")
    index.add_argument(
        "--key", default="", help="keypair file from `agentd bundle keygen` (signs entries)"
    )
    index.set_defaults(func=run_index)

    serve = sub.add_parser("serve", help="serve a registry directory over http (local marketplace)")
    serve.add_argument("directory")
    serve.add_argument("--port", type=int, default=8877)
    serve.set_defaults(func=run_serve)

    keygen = sub.add_parser("keygen", help="generate an ed25519 signing keypair")
    keygen.add_argument(
        "--out",
        default="agentd-publisher-key.json",
        help="keypair file (KEEP PRIVATE; only publisher_key goes public)",
    )
    keygen.set_defaults(func=run_keygen)


def _load_bundle_toml(agent_dir: Path) -> dict:
    path = agent_dir / "bundle.toml"
    if not path.is_file():
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8")).get("bundle") or {}


def run_pack(args: argparse.Namespace) -> int:
    from agentd.config import load_config
    from agentd.infrastructure.marketplace import bundle_io

    agent_dir = Path(args.agent_dir).resolve()
    if not agent_dir.is_dir():
        print(f"not a directory: {agent_dir}")
        return 1
    declared = _load_bundle_toml(agent_dir)
    bundle_id = str(declared.get("id") or agent_dir.name)
    version = args.version or str(declared.get("version") or "1.0.0")

    deps: list[PluginDep] = []
    if declared:  # bundle.toml is the source of truth for declared deps
        deps = list(
            parse_bundle_manifest(
                {"bundle": {**declared, "id": bundle_id, "version": version}}
            ).plugins
        )
    vendor_ids = [p for p in (args.vendor_plugins or "").split(",") if p.strip()]
    builtin_ids = [p for p in (args.builtin_plugins or "").split(",") if p.strip()]
    have = {d.id for d in deps}
    deps += [
        PluginDep(id=p.strip(), source="vendored") for p in vendor_ids if p.strip() not in have
    ]
    deps += [
        PluginDep(id=p.strip(), source="builtin") for p in builtin_ids if p.strip() not in have
    ]

    config = load_config()
    plugin_roots = [Path(config.plugins_dir), Path(config.builtin_plugins_dir)]
    vendored_dirs: dict[str, Path] = {}
    for dep in deps:
        if dep.source != "vendored":
            continue
        source_dir = next(
            (r / dep.id for r in plugin_roots if (r / dep.id / "plugin.toml").is_file()), None
        )
        if source_dir is None:
            print(f"vendored plugin '{dep.id}' not found in {', '.join(map(str, plugin_roots))}")
            return 1
        vendored_dirs[dep.id] = source_dir

    manifest = BundleManifest(
        id=bundle_id,
        name=str(declared.get("name") or bundle_id),
        version=version,
        description=str(declared.get("description") or ""),
        agentd_compat=str(declared.get("agentd_compat") or ""),
        entitlement=str(declared.get("entitlement") or ""),
        publisher=str(declared.get("publisher") or ""),
        icon=str(declared.get("icon") or ""),
        plugins=tuple(deps),
    )
    package_path = bundle_io.pack_bundle(agent_dir, Path(args.out), manifest, vendored_dirs)
    print(f"packed: {package_path}  ({package_path.stat().st_size:,} bytes)")
    print(f"sha256: {bundle_io.sha256_file(package_path)}")
    return 0


def run_index(args: argparse.Namespace) -> int:
    import json

    from agentd.infrastructure.marketplace.index_builder import build_index

    private_b64 = public_b64 = ""
    if args.key:
        keypair = json.loads(Path(args.key).read_text(encoding="utf-8"))
        private_b64, public_b64 = keypair["private_key"], keypair["public_key"]
    index_path = build_index(
        Path(args.directory),
        name=args.name,
        publisher=args.publisher,
        private_key_b64=private_b64,
        public_key_b64=public_b64,
    )
    print(f"wrote {index_path}" + ("  (signed)" if private_b64 else "  (unsigned)"))
    return 0


def run_serve(args: argparse.Namespace) -> int:
    import functools
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    directory = str(Path(args.directory).resolve())
    handler = functools.partial(SimpleHTTPRequestHandler, directory=directory)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    print(f"local registry: http://127.0.0.1:{args.port}/index.json  (serving {directory})")
    print(f"point an install at it:  agentd install <id> --registry http://127.0.0.1:{args.port}")
    print("Ctrl-C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def run_keygen(args: argparse.Namespace) -> int:
    import json

    from agentd.infrastructure import signing

    private_b64, public_b64 = signing.generate_keypair()
    out = Path(args.out)
    if out.exists():
        print(f"refusing to overwrite existing keyfile: {out}")
        return 1
    out.write_text(
        json.dumps(
            {
                "type": "agentd-publisher-keypair",
                "private_key": private_b64,
                "public_key": public_b64,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"keypair written to {out} — KEEP THIS FILE PRIVATE")
    print(f"publisher_key (public, for distribution.toml / index): {public_b64}")
    return 0

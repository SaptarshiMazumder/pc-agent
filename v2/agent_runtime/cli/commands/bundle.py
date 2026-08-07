"""`agentd bundle ...` — PUBLISHER tooling (us, and later third parties):

  pack    an agents/<id>/ directory (+ vendored plugins) -> <id>-<ver>.agentpkg
  index   a directory of .agentpkg files -> index.json  (= a complete registry)
  publish pack + sign + upload, in one command — the whole release
  serve   that directory over http://localhost — a real local marketplace
  keygen  an ed25519 keypair for signing indexes (M7)

The `pack` defaults come from an optional `bundle.toml` inside the agent dir, so a
bundle's identity lives WITH the agent (flags override)."""

from __future__ import annotations

import argparse
import tomllib
from pathlib import Path

from agent_runtime.domain.bundle import BundleManifest, PluginDep, parse_bundle_manifest


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

    publish = sub.add_parser(
        "publish",
        help="pack agents, sign the index, upload to a registry (directory or s3://bucket)",
        description="Release an agent to a marketplace in one command: pack each agent dir, merge "
        "into the registry's EXISTING index (so this adds rather than replaces), sign, and upload.",
    )
    publish.add_argument("agent_dir", nargs="+", help="agent directories to publish")
    publish.add_argument(
        "--to",
        default="",
        help="registry target: a directory path, or s3://bucket[/prefix] (env AGENTD_PUBLISH_TARGET)",
    )
    publish.add_argument(
        "--key",
        default="",
        help="keypair file from `agentd bundle keygen` (env AGENTD_PUBLISHER_KEYFILE). Required "
        "unless the registry is unsigned, or --unsigned is passed",
    )
    publish.add_argument("--name", default="", help="registry display name (default: keep existing)")
    publish.add_argument("--publisher", default="", help="publisher name (default: keep existing)")
    publish.add_argument(
        "--version", default="", help="version for every agent packed (default from each bundle.toml)"
    )
    publish.add_argument(
        "--rotate-key",
        action="store_true",
        help="allow publishing with a DIFFERENT key than the registry's current one (breaks every "
        "already-installed client pinned to the old key)",
    )
    publish.add_argument(
        "--unsigned",
        action="store_true",
        help="publish without signatures (local testing only)",
    )
    publish.add_argument(
        "--dry-run", action="store_true", help="build everything, upload nothing, print the plan"
    )
    publish.set_defaults(func=run_publish)

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


def _pack_agent_dir(
    agent_dir: Path,
    out_dir: Path,
    version: str = "",
    vendor_plugins: str = "",
    builtin_plugins: str = "",
) -> Path:
    """agent directory -> .agentpkg in out_dir. Raises ValueError with a user-facing message.

    Shared by `pack` and `publish`. Publishing assembling its own manifest would mean two
    definitions of what a bundle IS, and they would drift — so what gets published is
    byte-for-byte what `pack` produces.
    """
    from agent_runtime.config import load_config
    from agent_runtime.infrastructure.marketplace import bundle_io

    if not agent_dir.is_dir():
        raise ValueError(f"not a directory: {agent_dir}")
    declared = _load_bundle_toml(agent_dir)
    bundle_id = str(declared.get("id") or agent_dir.name)
    version = version or str(declared.get("version") or "1.0.0")

    deps: list[PluginDep] = []
    if declared:  # bundle.toml is the source of truth for declared deps
        deps = list(
            parse_bundle_manifest(
                {"bundle": {**declared, "id": bundle_id, "version": version}}
            ).plugins
        )
    vendor_ids = [p for p in (vendor_plugins or "").split(",") if p.strip()]
    builtin_ids = [p for p in (builtin_plugins or "").split(",") if p.strip()]
    have = {d.id for d in deps}
    deps += [
        PluginDep(id=p.strip(), source="vendored") for p in vendor_ids if p.strip() not in have
    ]
    deps += [
        PluginDep(id=p.strip(), source="builtin") for p in builtin_ids if p.strip() not in have
    ]

    # Only VENDORED deps need this install's plugin roots, so only they need the config. Loading it
    # unconditionally made packing depend on the ambient environment for no reason — a bundle with
    # no vendored plugins is a pure function of its directory.
    vendored_dirs: dict[str, Path] = {}
    vendored = [d for d in deps if d.source == "vendored"]
    if vendored:
        config = load_config()
        plugin_roots = [Path(config.plugins_dir), Path(config.builtin_plugins_dir)]
        for dep in vendored:
            source_dir = next(
                (r / dep.id for r in plugin_roots if (r / dep.id / "plugin.toml").is_file()), None
            )
            if source_dir is None:
                raise ValueError(
                    f"vendored plugin '{dep.id}' not found in {', '.join(map(str, plugin_roots))}"
                )
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
    return bundle_io.pack_bundle(agent_dir, out_dir, manifest, vendored_dirs)


def run_pack(args: argparse.Namespace) -> int:
    from agent_runtime.infrastructure.marketplace import bundle_io

    try:
        package_path = _pack_agent_dir(
            Path(args.agent_dir).resolve(),
            Path(args.out),
            args.version,
            args.vendor_plugins,
            args.builtin_plugins,
        )
    except ValueError as e:
        print(e)
        return 1
    print(f"packed: {package_path}  ({package_path.stat().st_size:,} bytes)")
    print(f"sha256: {bundle_io.sha256_file(package_path)}")
    return 0


def run_index(args: argparse.Namespace) -> int:
    import json

    from agent_runtime.infrastructure.marketplace.index_builder import build_index

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


# ─────────────────────────────── publish ───────────────────────────────
# A registry is two things in one place: the .agentpkg artifacts, and an index.json that lists them
# and carries the signatures. So publishing is never just an upload — it is a read-modify-write of
# the index, and the READ is the step that is easy to skip and expensive to skip (build_index's
# `carry_entries` docstring has the failure). Everything below exists to make that one command.

_S3_SCHEME = "s3://"


def _split_s3(target: str) -> tuple[str, str]:
    """``s3://bucket/prefix/`` -> ``("bucket", "prefix")``; prefix may be empty."""
    bucket, _, prefix = target[len(_S3_SCHEME) :].strip("/").partition("/")
    return bucket, prefix.strip("/")


def _s3_uri(bucket: str, prefix: str, name: str) -> str:
    return f"s3://{bucket}/{prefix}/{name}" if prefix else f"s3://{bucket}/{name}"


def _aws(*args: str) -> tuple[int | None, str]:
    """Run the aws CLI. Returns (exit code, stdout); code None means the CLI is not installed.

    Shelling out rather than adding boto3: this is the only place in the runtime that would need
    it, publishers already have the CLI configured (it is how the registry bucket got made), and a
    ~15MB dependency on every desktop install to serve a publisher-only command is a bad trade.
    """
    import subprocess

    try:
        done = subprocess.run(["aws", *args], capture_output=True, text=True)  # noqa: S603
    except FileNotFoundError:
        print(
            "the `aws` CLI is not on PATH, and an s3:// target needs it.\n"
            "  * install it, or\n"
            "  * publish into a local directory with --to <dir> and sync that directory yourself."
        )
        return None, ""
    if done.returncode != 0 and done.stderr:
        print(done.stderr.strip())
    return done.returncode, done.stdout


def _read_registry_index(target: str, is_s3: bool) -> dict | None:
    """The registry's CURRENT index. {} = none yet (new registry); None = do not proceed."""
    import json

    if is_s3:
        bucket, prefix = _split_s3(target)
        code, out = _aws("s3", "cp", _s3_uri(bucket, prefix, "index.json"), "-")
        if code is None:
            return None
        if code != 0:
            print("no index.json at the target yet — publishing a NEW registry")
            return {}
        raw = out
    else:
        path = Path(target).expanduser().resolve() / "index.json"
        if not path.is_file():
            print(f"no {path} yet — publishing a NEW registry")
            return {}
        raw = path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw or "{}")
    except ValueError as e:
        # Do NOT fall back to "treat it as empty". That would rebuild the index from only what is
        # being published now and silently unpublish everything already in the registry — the exact
        # accident carry_entries exists to prevent, arrived at from the other direction.
        print(
            f"the registry's current index.json is not valid JSON ({e}).\n"
            "Refusing to overwrite it: a bad read here would unpublish every bundle it lists. "
            "Fix or remove it, then publish again."
        )
        return None
    if not isinstance(data, dict):
        print("the registry's current index.json is not a JSON object — refusing to overwrite it.")
        return None
    return data


def _upload_registry(staging: Path, target: str, is_s3: bool) -> int:
    """Push the built registry. Artifacts first, index.json LAST — always."""
    import shutil

    packages = sorted(staging.glob("*.agentpkg"))
    index = staging / "index.json"
    # The index is how a client FINDS an artifact, so publishing it before its artifacts exist
    # opens a window where the store lists a bundle whose download 404s. Ordering the writes costs
    # nothing and closes it.
    if is_s3:
        bucket, prefix = _split_s3(target)
        for package in packages:
            code, _ = _aws(
                "s3", "cp", str(package), _s3_uri(bucket, prefix, package.name),
                "--content-type", "application/octet-stream",
            )
            if code != 0:
                return 1
            print(f"  uploaded {package.name}")
        code, _ = _aws(
            "s3", "cp", str(index), _s3_uri(bucket, prefix, "index.json"),
            "--content-type", "application/json",
            # index.json is read on every store open and changes on every publish. Without this a
            # CDN or browser can serve a stale listing for hours, so a successful publish looks
            # like it did nothing.
            "--cache-control", "no-cache",
        )
        if code != 0:
            return 1
        print("  uploaded index.json")
        return 0

    destination = Path(target).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    for package in packages:
        shutil.copy2(package, destination / package.name)
        print(f"  wrote {package.name}")
    shutil.copy2(index, destination / "index.json")
    print("  wrote index.json")
    return 0


def run_publish(args: argparse.Namespace) -> int:
    import json
    import os
    import tempfile

    from agent_runtime.infrastructure.marketplace.index_builder import build_index

    target = (args.to or os.environ.get("AGENTD_PUBLISH_TARGET", "")).strip()
    if not target:
        print(
            "no registry target. Pass --to <directory|s3://bucket[/prefix]>, "
            "or set AGENTD_PUBLISH_TARGET."
        )
        return 1
    is_s3 = target.startswith(_S3_SCHEME)

    key_file = (args.key or os.environ.get("AGENTD_PUBLISHER_KEYFILE", "")).strip()
    private_b64 = public_b64 = ""
    if key_file:
        try:
            keypair = json.loads(Path(key_file).read_text(encoding="utf-8"))
            private_b64 = str(keypair["private_key"])
            public_b64 = str(keypair["public_key"])
        except (OSError, ValueError, KeyError) as e:
            print(f"cannot read keypair {key_file}: {e}")
            return 1
    elif not args.unsigned:
        # Unsigned has to be asked for. A registry that is accidentally unsigned looks completely
        # healthy — stores list, installs succeed — and the guarantee it lost only matters on the
        # day someone rewrites index.json, at which point nothing rejects it.
        print(
            "refusing to publish without a signing key.\n"
            "  agentd bundle keygen --out <keypair.json>     # once; keep it outside the repo\n"
            "  agentd bundle publish ... --key <keypair.json>\n"
            "Pass --unsigned only for a throwaway local registry."
        )
        return 1

    existing = _read_registry_index(target, is_s3)
    if existing is None:
        return 1
    prior_key = str(existing.get("publisher_key") or "")
    prior_entries = tuple(existing.get("bundles") or [])

    # Two ways to break every client that already trusts this registry. Both are one flag away from
    # being intentional, and neither should be reachable by accident.
    if prior_key and not private_b64:
        print(
            f"this registry is SIGNED (publisher_key {prior_key[:12]}…) and you are publishing "
            "unsigned.\nEvery client pinned to that key would reject the whole registry. "
            "Pass --key with the matching keypair."
        )
        return 1
    if prior_key and public_b64 and prior_key != public_b64 and not args.rotate_key:
        print(
            "KEY MISMATCH — refusing to publish.\n"
            f"  registry is signed by: {prior_key}\n"
            f"  your keypair's public: {public_b64}\n"
            "Publishing would re-sign the registry with a key no installed client trusts, and the "
            "symptom is every download failing verification while the store still lists fine.\n"
            "Use the original keypair, or pass --rotate-key AND update publisher_key in "
            "v2/clients/desktop/flavors/*/distribution.toml plus registry_publisher_key in "
            "v2/infra/environments/<env>/main.tf (existing installs will need the new build)."
        )
        return 1

    with tempfile.TemporaryDirectory(prefix="agentd-publish-") as work:
        staging = Path(work)
        packed: list[Path] = []
        for raw_dir in args.agent_dir:
            try:
                package = _pack_agent_dir(Path(raw_dir).resolve(), staging, args.version)
            except ValueError as e:
                print(e)
                return 1
            packed.append(package)
            print(f"packed {package.name}  ({package.stat().st_size:,} bytes)")

        index_path = build_index(
            staging,
            # Default to the registry's existing identity: publishing an agent should not quietly
            # rename the marketplace because a flag was omitted.
            name=args.name or str(existing.get("name") or "agentd marketplace"),
            publisher=args.publisher or str(existing.get("publisher") or ""),
            private_key_b64=private_b64,
            public_key_b64=public_b64,
            carry_entries=prior_entries,
        )
        index = json.loads(index_path.read_text(encoding="utf-8"))
        listed = [f"{b.get('id')} {b.get('version')}" for b in index.get("bundles", [])]
        print(
            f"\nindex: {len(listed)} bundle(s) — {', '.join(listed)}"
            + ("  (signed)" if private_b64 else "  (UNSIGNED)")
        )

        if args.dry_run:
            print(f"\n--dry-run: nothing uploaded. Would publish to {target}")
            print(index_path.read_text(encoding="utf-8"))
            return 0

        print(f"\npublishing to {target}")
        if _upload_registry(staging, target, is_s3) != 0:
            print("upload FAILED — the index was not published, so the registry is unchanged.")
            return 1

    print("\npublished.")
    if is_s3:
        print("Stores pick it up on their next refresh (the desktop Store's Refresh button).")
    else:
        print(f"Serve it locally:  agentd bundle serve {target}")
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

    from agent_runtime.infrastructure import signing

    private_b64, public_b64 = signing.generate_keypair()
    out = Path(args.out)
    if out.exists():
        print(f"refusing to overwrite existing keyfile: {out}")
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
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

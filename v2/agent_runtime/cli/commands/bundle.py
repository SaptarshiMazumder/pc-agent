"""`agentd bundle ...` — PUBLISHER tooling (us, and third-party creators):

  pack    an agents/<id>/ directory (+ vendored plugins) -> <id>-<ver>.agentpkg
  index   a directory of .agentpkg files -> index.json  (= a complete registry)
  publish pack + sign + upload, in one command — the whole release
  serve   that directory over http://localhost — a real local marketplace
  keygen  an ed25519 keypair for signing indexes (M7)
  roster  the trusted-creator list a multi-creator registry runs on

The `pack` defaults come from an optional `bundle.toml` inside the agent dir, so a
bundle's identity lives WITH the agent (flags override).

ONE REGISTRY, TWO SHAPES. A single-vendor registry needs `keygen` + `publish --key` and nothing
else. A registry with more than one creator needs the roster: the PLATFORM ROOT key signs who is
trusted, each creator's own key signs their own bundles, and clients pin only the root key — so
adding a creator never means rebuilding and reshipping installed apps. The two shapes share every
command; supplying `--roster` is what switches the output to schema 2."""

from __future__ import annotations

import argparse
from datetime import UTC
from pathlib import Path

from agent_runtime.infrastructure.marketplace import packer


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
    publish.add_argument(
        "agent_dir",
        nargs="*",
        help="agent directories to publish (may be empty when only --engine is given)",
    )
    publish.add_argument(
        "--engine",
        default="",
        help="ALSO publish this shared-engine installer (the core build's Setup exe). Per-agent "
        "stub installers download the engine from the registry, so it has to be listed there. "
        "Renamed to the registry convention on the way in.",
    )
    publish.add_argument(
        "--engine-version",
        default="",
        help="the engine's version (required unless the file is already named "
        "agentd-engine-<version>-setup.exe)",
    )
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
        "--publisher-id",
        default="",
        help="YOUR creator id on the registry's roster (multi-creator registries). Your bundles "
        "are stamped with it so clients verify them against your key and nobody else's.",
    )
    publish.add_argument(
        "--roster",
        default="",
        help="the signed roster file from `agentd bundle roster` — publishes a schema-2 "
        "(multi-creator) index. Omit to keep the roster already on the registry.",
    )
    publish.add_argument(
        "--no-installers",
        action="store_true",
        help="publish only the .agentpkg bundles. By default any installer already delivered to "
        "agents/<id>/clients/desktop/ is published too, so someone with no agentd yet has "
        "something to download.",
    )
    publish.add_argument(
        "--dry-run", action="store_true", help="build everything, upload nothing, print the plan"
    )
    publish.set_defaults(func=run_publish)

    roster = sub.add_parser(
        "roster",
        help="manage the trusted-creator roster (signed by the PLATFORM ROOT key)",
        description="The roster is the only thing the root key signs. Creators sign their own "
        "bundles with their own keys, which never leave them — so this is the one command that "
        "needs the root private key, and it should run where that key lives (CI) and nowhere else.",
    )
    roster_sub = roster.add_subparsers(dest="roster_command", required=True)

    roster_add = roster_sub.add_parser("add", help="add or re-key a creator, then re-sign")
    roster_add.add_argument("--root-key", required=True, help="the PLATFORM ROOT keypair file")
    roster_add.add_argument("--id", required=True, help="the creator's stable id (e.g. 'acme')")
    roster_add.add_argument("--key", required=True, help="the creator's PUBLIC key (base64)")
    roster_add.add_argument("--name", default="", help="display name (default: the id)")
    roster_add.add_argument("--file", default="registry-roster.json", help="the roster file")
    roster_add.set_defaults(func=run_roster_add)

    roster_revoke = roster_sub.add_parser("revoke", help="revoke a creator, then re-sign")
    roster_revoke.add_argument("--root-key", required=True, help="the PLATFORM ROOT keypair file")
    roster_revoke.add_argument("--id", required=True, help="the creator id to revoke")
    roster_revoke.add_argument("--file", default="registry-roster.json", help="the roster file")
    roster_revoke.set_defaults(func=run_roster_revoke)

    # The publish SERVICE mints a creator identity on someone's first upload and records it as
    # PENDING, because admitting them means re-signing the roster with the offline root key — which
    # cannot be automated without putting that key online. These two commands are that review step.
    roster_pending = roster_sub.add_parser(
        "pending",
        help="list creators awaiting admission (reads the publish service's table)",
        description="Who has tried to publish and is waiting to be let onto the roster. Their "
        "bundles are already uploaded and signed with their own key; nothing verifies until the "
        "roster names them.",
    )
    roster_pending.add_argument(
        "--creators-table",
        default="",
        help="DynamoDB table name (default: $AGENTD_CREATORS_TABLE; terraform output "
        "publish_creators_table)",
    )
    roster_pending.add_argument("--region", default="", help="AWS region (default: the profile's)")
    roster_pending.set_defaults(func=run_roster_pending)

    roster_admit = roster_sub.add_parser(
        "admit",
        help="admit pending creators: add them to the roster, re-sign it, and mark them listed",
        description="ONE command for the whole review step. It reads the pending creators, adds "
        "each to the roster file with the public key the service generated for them, re-signs the "
        "roster with the ROOT key, and only then marks them listed in the table — in that order, "
        "because a creator marked listed before the roster is published would have their bundles "
        "advertised with a key no client trusts yet.",
    )
    roster_admit.add_argument("--root-key", required=True, help="the PLATFORM ROOT keypair file")
    roster_admit.add_argument(
        "--id", default="", action="append", help="admit only this creator id (repeatable)"
    )
    roster_admit.add_argument("--file", default="registry-roster.json", help="the roster file")
    roster_admit.add_argument("--creators-table", default="", help="DynamoDB table name")
    roster_admit.add_argument("--region", default="", help="AWS region")
    roster_admit.add_argument(
        "--dry-run", action="store_true", help="show who would be admitted, change nothing"
    )
    roster_admit.set_defaults(func=run_roster_admit)

    roster_show = roster_sub.add_parser("show", help="print a roster and check its signature")
    roster_show.add_argument("file", nargs="?", default="registry-roster.json")
    roster_show.add_argument(
        "--root-key",
        default="",
        help="verify the signature against this PUBLIC key (base64) or keypair file",
    )
    roster_show.set_defaults(func=run_roster_show)

    roster_publish = roster_sub.add_parser(
        "publish",
        help="push the roster to a registry WITHOUT publishing any bundle",
        description="A roster edit only takes effect once the registry serves it. Adding a creator "
        "can ride along with their first publish; a REVOCATION cannot — the creator being revoked "
        "is exactly the person who can no longer publish. So the platform pushes the roster on its "
        "own, and this is that command.",
    )
    roster_publish.add_argument(
        "--to", default="", help="registry target: a directory, or s3://bucket[/prefix]"
    )
    roster_publish.add_argument("--file", default="registry-roster.json", help="the roster file")
    roster_publish.add_argument(
        "--dry-run", action="store_true", help="show what would change, upload nothing"
    )
    roster_publish.set_defaults(func=run_roster_publish)

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


# Packing itself lives in infrastructure/marketplace/packer.py, not here. It was private to this
# module until the product builder and the publish service needed the same function; a command
# module is not somewhere an application service can import from. These aliases keep the existing
# call sites (and their tests) working while there is exactly one implementation.
_load_bundle_toml = packer.load_bundle_toml
_load_agent_toml = packer.load_agent_toml
_first = packer.first
_pack_agent_dir = packer.pack_agent_dir


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
    import os
    import subprocess

    # Publishing an INSTALLER means a ~250MB multipart upload, and a single dropped part fails the
    # whole command ("Connection was closed before we received a valid response ... partNumber=14")
    # after minutes of transfer. The default retry mode gives up early on exactly this.
    #
    # `adaptive` retries individual parts with backoff, so a brief network blip costs seconds
    # instead of the whole upload. Set in the child's environment rather than as flags because
    # these are config knobs, not `s3 cp` arguments, and the caller's own AWS_* settings still
    # win — an operator who has tuned this deliberately is not overridden.
    env = dict(os.environ)
    env.setdefault("AWS_RETRY_MODE", "adaptive")
    env.setdefault("AWS_MAX_ATTEMPTS", "10")

    try:
        done = subprocess.run(["aws", *args], capture_output=True, text=True, env=env)  # noqa: S603
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


def _stage_installers(agent_dir: Path, staging: Path, bundle_id: str, version: str) -> list[Path]:
    """Copy this agent's DELIVERED installer(s) into the registry staging dir, renamed to the
    convention `index_builder` discovers.

    Discovered rather than passed as a flag, so one publish command works whether or not an
    installer was ever built for the agent. `dist-app.mjs` delivers to
    ``agents/<id>/clients/desktop/<Product> Setup <ver>.exe`` — a product-name-shaped filename
    with nothing in it to key on, and ``clients/`` is excluded from the .agentpkg, so the file is
    neither inside the bundle nor findable by id without this step.

    A file whose name contains the version being published WINS. Otherwise the newest is used and
    a warning is printed: silently renaming last month's 0.2.0 build to `…-0.3.0-setup.exe` would
    publish an installer that lies about its own version, and the download would then install an
    agent the store card says is newer.
    """
    import shutil

    from agent_runtime.infrastructure.marketplace.index_builder import (
        PLATFORM_BY_EXT,
        installer_name,
    )

    source_dir = agent_dir / "clients" / "desktop"
    if not source_dir.is_dir():
        return []
    staged: list[Path] = []
    for suffix in sorted(PLATFORM_BY_EXT):
        found = [p for p in source_dir.glob(f"*{suffix}") if p.is_file()]
        if not found:
            continue
        exact = [p for p in found if version in p.name]
        if exact:
            chosen = max(exact, key=lambda p: p.stat().st_mtime)
        else:
            chosen = max(found, key=lambda p: p.stat().st_mtime)
            print(
                f"  WARNING: no {suffix} installer in {source_dir} names version {version}; "
                f"using the newest ({chosen.name}). Rebuild it if that is not {version}."
            )
        dest = staging / installer_name(bundle_id, version, suffix)
        shutil.copy2(chosen, dest)
        staged.append(dest)
        print(f"  staged installer {chosen.name} -> {dest.name} ({dest.stat().st_size:,} bytes)")
    return staged


def _stage_engine(source: str, version: str, staging: Path) -> int:
    """Copy the shared-engine installer into staging under the registry's naming convention.

    The engine is what a per-agent STUB downloads on a machine with no agentd, so it has to be an
    artifact in the registry like any other. Renaming happens here rather than asking the publisher
    to rename by hand, because the convention is what `_engines` matches on and a near-miss
    (`agentd Setup 0.2.0.exe`) is silently ignored — publishing would look like it worked and every
    stub built afterwards would have no engine to fetch.
    """
    import re
    import shutil

    from agent_runtime.infrastructure.marketplace.index_builder import (
        ENGINE_BUNDLE_ID,
        PLATFORM_BY_EXT,
        installer_name,
    )

    path = Path(source).expanduser().resolve()
    if not path.is_file():
        print(f"--engine: no such file: {path}")
        return 1
    suffix = path.suffix.lower()
    if suffix not in PLATFORM_BY_EXT:
        print(f"--engine: {path.name} is not an installer (want one of {', '.join(sorted(PLATFORM_BY_EXT))})")
        return 1

    if not version:
        # Already conventionally named? Then its version is not something to ask for twice.
        match = re.match(rf"^{re.escape(ENGINE_BUNDLE_ID)}-(.+)-setup$", path.stem)
        if match:
            version = match.group(1)
        else:
            # electron-builder's default output is "agentd Setup 0.2.0.exe" — the number is right
            # there, and guessing it wrong would publish an engine that a payload's minimum-version
            # check then rejects. So it is read, and refused when it cannot be.
            loose = re.search(r"(\d+\.\d+[\w.\-+]*)", path.stem)
            if not loose:
                print(
                    f"--engine: cannot tell what version {path.name} is. Pass --engine-version."
                )
                return 1
            version = loose.group(1)
            print(f"  engine version read from the file name: {version}")

    dest = staging / installer_name(ENGINE_BUNDLE_ID, version, suffix)
    shutil.copy2(path, dest)
    print(f"  staged engine {path.name} -> {dest.name} ({dest.stat().st_size:,} bytes)")
    return 0


def _installer_files(staging: Path) -> list[Path]:
    """Installer artifacts in the staging dir — everything build_index may have referenced."""
    from agent_runtime.infrastructure.marketplace.index_builder import (
        INSTALLER_MARKER,
        PLATFORM_BY_EXT,
    )

    return sorted(
        p
        for p in staging.iterdir()
        if p.is_file()
        and p.suffix.lower() in PLATFORM_BY_EXT
        and INSTALLER_MARKER in p.stem.lower()
    )


def _upload_registry(staging: Path, target: str, is_s3: bool) -> int:
    """Push the built registry. Artifacts first, index.json LAST — always."""
    import shutil

    # Installers ride with the bundles: same ordering rule, same reason. The index is what makes a
    # download URL public, so every artifact it names must already be there.
    packages = sorted(staging.glob("*.agentpkg")) + _installer_files(staging)
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


def _resolve_roster(args: argparse.Namespace, existing: dict, prior_key: str):
    """-> (roster block | None | False, root_key). False means "stated but unreadable, stop".

    A registry that ALREADY has a roster keeps it when `--roster` is omitted. Dropping it would be
    a one-command demotion of a multi-creator registry back to a single key — every other creator
    silently un-trusted, their bundles rejected on every install, with no error at publish time.
    """
    from agent_runtime.domain.bundle import BundleError
    from agent_runtime.infrastructure.marketplace import roster_builder

    block = None
    if args.roster:
        try:
            block = roster_builder.read_roster_file(Path(args.roster))
        except BundleError as e:
            print(e)
            return False, ""
    elif int(existing.get("schema") or 1) >= 2:
        block = existing.get("publishers") or {}
        print("this registry has a creator roster — carrying it forward unchanged")
    if block is None:
        return None, ""
    return block, str(block.get("root_key") or "") or prior_key


def _creator_check(roster_block: dict, publisher_id: str, public_b64: str, private_b64: str) -> str:
    """The refusals that stop a publish which would look successful and fail on every install.

    All three failures below produce the same user-visible symptom — the store lists the bundle and
    every download fails signature verification — so they are worth catching here, where the cause
    is still obvious, instead of in a support thread.
    """
    if not private_b64:
        return (
            "this registry has a creator roster, so bundles must be SIGNED with your own key.\n"
            "  agentd bundle keygen --out my-creator-key.json\n"
            "  (ask the platform to add its public key with `agentd bundle roster add`)\n"
            "  agentd bundle publish ... --key my-creator-key.json --publisher-id <your id>"
        )
    if not publisher_id:
        return (
            "this registry has a creator roster, so --publisher-id is required.\n"
            "Without it your bundles would be published unattributed, and clients would try to "
            "verify them against the PLATFORM ROOT key — which never signs bundles — so every "
            "install would fail."
        )
    entries = {str(e.get("id") or ""): e for e in (roster_block.get("roster") or [])}
    if publisher_id in {str(r) for r in (roster_block.get("revoked") or [])}:
        return f"creator '{publisher_id}' is REVOKED on this roster — nothing you publish would install."
    entry = entries.get(publisher_id)
    if entry is None:
        known = ", ".join(sorted(entries)) or "(none)"
        return (
            f"creator '{publisher_id}' is not on this registry's roster (it lists: {known}).\n"
            "The platform adds you once, with your PUBLIC key:\n"
            f"  agentd bundle roster add --root-key <root.json> --id {publisher_id} --key {public_b64}"
        )
    if str(entry.get("key") or "") != public_b64:
        return (
            f"KEY MISMATCH for creator '{publisher_id}' — refusing to publish.\n"
            f"  the roster lists: {str(entry.get('key') or '')}\n"
            f"  your keypair is:  {public_b64}\n"
            "Use the keypair the roster knows, or have the platform re-key you:\n"
            f"  agentd bundle roster add --root-key <root.json> --id {publisher_id} --key {public_b64}"
        )
    return ""


def run_publish(args: argparse.Namespace) -> int:
    import json
    import os
    import tempfile

    from agent_runtime.infrastructure.marketplace import bundle_io, index_builder
    from agent_runtime.infrastructure.marketplace.index_builder import build_index

    if not args.agent_dir and not getattr(args, "engine", ""):
        print("nothing to publish: give one or more agent directories, and/or --engine <installer>.")
        return 1

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
    # The engine rows get carried for exactly the reason the bundle entries do. Publishing one
    # agent from a staging directory with no engine installer in it would otherwise drop the
    # `engine` block, and every per-agent STUB built after that would have no engine to fetch —
    # new installs break completely, with nothing in the output to say so.
    prior_engines = tuple(existing.get("engine") or [])

    # Is this a multi-creator registry? Either because a roster was passed, or because the registry
    # already is one — in which case publishing WITHOUT the roster would drop it and demote the
    # whole registry back to a single key, un-trusting every other creator in one command.
    roster_block, root_key_b64 = _resolve_roster(args, existing, prior_key)
    if roster_block is False:  # a stated roster that could not be read; the reason is printed
        return 1

    if roster_block is not None:
        failure = _creator_check(roster_block, args.publisher_id, public_b64, private_b64)
        if failure:
            print(failure)
            return 1
    else:
        # Single-key registry. Two ways to break every client that already trusts it, both one flag
        # away from being intentional, and neither reachable by accident.
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
                "Publishing would re-sign the registry with a key no installed client trusts, and "
                "the symptom is every download failing verification while the store still lists "
                "fine.\n"
                "Either use the original keypair, or — if this registry now has more than one "
                "creator — put your key on its roster instead of replacing the registry's:\n"
                f"  agentd bundle roster add --root-key <root.json> --id <you> --key {public_b64}\n"
                "  agentd bundle publish ... --publisher-id <you> --roster registry-roster.json\n"
                "The blunt alternative is --rotate-key AND updating publisher_key in "
                "v2/clients/desktop/flavors/*/distribution.toml plus registry_publisher_key in "
                "v2/infra/environments/<env>/main.tf (existing installs will need the new build)."
            )
            return 1

    with tempfile.TemporaryDirectory(prefix="agentd-publish-") as work:
        staging = Path(work)
        packed: list[Path] = []
        for raw_dir in args.agent_dir:
            agent_dir = Path(raw_dir).resolve()
            try:
                package = _pack_agent_dir(agent_dir, staging, args.version)
            except ValueError as e:
                print(e)
                return 1
            packed.append(package)
            print(f"packed {package.name}  ({package.stat().st_size:,} bytes)")
            if not args.no_installers:
                manifest = bundle_io.read_manifest(package)
                _stage_installers(agent_dir, staging, manifest.id, manifest.version)

        if getattr(args, "engine", ""):
            if _stage_engine(args.engine, getattr(args, "engine_version", ""), staging) != 0:
                return 1

        index_path = build_index(
            staging,
            # Default to the registry's existing identity: publishing an agent should not quietly
            # rename the marketplace because a flag was omitted.
            name=args.name or str(existing.get("name") or "agentd marketplace"),
            publisher=args.publisher or str(existing.get("publisher") or ""),
            private_key_b64=private_b64,
            public_key_b64=public_b64,
            carry_entries=prior_entries,
            publisher_id=args.publisher_id if roster_block is not None else "",
            roster=roster_block,
            root_key_b64=root_key_b64,
            carry_engines=prior_engines,
        )
        index = json.loads(index_path.read_text(encoding="utf-8"))
        listed = [f"{b.get('id')} {b.get('version')}" for b in index.get("bundles", [])]
        engines = index.get("engine") or []
        print(
            f"\nindex: {len(listed)} bundle(s) — {', '.join(listed)}"
            + ("  (signed)" if private_b64 else "  (UNSIGNED)")
        )
        if engines:
            print(
                "engine: "
                + ", ".join(f"{e.get('platform')} {e.get('version') or '?'}" for e in engines)
                + "  (per-agent stubs install this)"
            )
        else:
            # Said out loud because it is invisible otherwise and it disables the whole small-stub
            # path: `agentd product build` will write payloads and no installers.
            print(
                "engine: NONE in this registry — per-agent stub installers cannot be built against "
                f"it. Publish one by putting {index_builder.ENGINE_BUNDLE_ID}-<version>-setup.exe in "
                "the staging directory."
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


# ─────────────────────────────── roster ───────────────────────────────


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_keypair(path: str) -> tuple[str, str]:
    """-> (private_b64, public_b64) from a `agentd bundle keygen` file."""
    import json

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return str(data["private_key"]), str(data["public_key"])


def _load_roster(path: str) -> dict:
    from agent_runtime.infrastructure.marketplace import roster_builder

    if not Path(path).is_file():
        return {}
    return roster_builder.read_roster_file(Path(path))


def run_roster_add(args: argparse.Namespace) -> int:
    from agent_runtime.domain.bundle import BundleError
    from agent_runtime.infrastructure.marketplace import roster_builder

    try:
        root_private, root_public = _read_keypair(args.root_key)
        block = roster_builder.with_publisher(
            _load_roster(args.file),
            publisher_id=args.id,
            name=args.name,
            key=args.key,
            issued=_now_iso(),
            root_private_b64=root_private,
            root_public_b64=root_public,
        )
    except (OSError, ValueError, KeyError, BundleError) as e:
        print(f"cannot update the roster: {e}")
        return 1
    roster_builder.write_roster_file(Path(args.file), block)
    print(f"roster updated: {args.id} is now trusted ({len(block.get('roster') or [])} creator(s))")
    print(f"wrote {args.file} — publish it with:  agentd bundle publish ... --roster {args.file}")
    return 0


def _creator_directory(args: argparse.Namespace):
    """The publish service's creator table, as a directory object. Operator-side only.

    boto3 is imported HERE rather than at module scope: `agentd bundle pack` must keep working on a
    machine that has never heard of AWS, and it is the same module.
    """
    import os

    table_name = (args.creators_table or os.environ.get("AGENTD_CREATORS_TABLE", "")).strip()
    if not table_name:
        raise ValueError(
            "no creators table. Pass --creators-table, or set AGENTD_CREATORS_TABLE "
            "(terraform output publish_creators_table)."
        )
    try:
        import boto3
    except ImportError as e:
        raise ValueError("boto3 is not installed — `pip install boto3` to run operator commands") from e

    from agent_runtime.infrastructure.publish.creator_directory import DynamoCreatorDirectory

    kwargs = {"region_name": args.region} if getattr(args, "region", "") else {}
    dynamodb = boto3.resource("dynamodb", **kwargs)
    return DynamoCreatorDirectory(dynamodb.Table(table_name), dynamodb.Table(table_name))


def run_roster_pending(args: argparse.Namespace) -> int:
    try:
        directory = _creator_directory(args)
        waiting = directory.pending()
    except ValueError as e:
        print(e)
        return 1
    if not waiting:
        print("no creators are waiting for admission.")
        return 0
    print(f"{len(waiting)} creator(s) awaiting admission:\n")
    for creator in waiting:
        print(f"  {creator.id}  {creator.name}")
        print(f"      key {creator.public_key}")
    print("\nAdmit them with:  agentd bundle roster admit --root-key <keypair>")
    return 0


def run_roster_admit(args: argparse.Namespace) -> int:
    """Add pending creators to the roster, re-sign it, then mark them listed. In that order."""
    from agent_runtime.domain.bundle import BundleError
    from agent_runtime.infrastructure.marketplace import roster_builder

    try:
        directory = _creator_directory(args)
        waiting = directory.pending()
    except ValueError as e:
        print(e)
        return 1

    wanted = {i for i in (args.id or []) if i}
    if wanted:
        waiting = [c for c in waiting if c.id in wanted]
        missing = wanted - {c.id for c in waiting}
        if missing:
            print(f"not pending (or unknown): {', '.join(sorted(missing))}")
            return 1
    if not waiting:
        print("nothing to admit.")
        return 0

    for creator in waiting:
        print(f"  admitting {creator.id}  {creator.name}")
    if args.dry_run:
        print(f"\n--dry-run: the roster and the table are unchanged.")
        return 0

    try:
        root_private, root_public = _read_keypair(args.root_key)
        block = _load_roster(args.file)
        for creator in waiting:
            if not creator.public_key:
                raise ValueError(f"{creator.id} has no public key recorded — cannot admit them")
            block = roster_builder.with_publisher(
                block,
                publisher_id=creator.id,
                name=creator.name,
                key=creator.public_key,
                issued=_now_iso(),
                root_private_b64=root_private,
                root_public_b64=root_public,
            )
    except (OSError, ValueError, KeyError, BundleError) as e:
        print(f"cannot update the roster: {e}")
        return 1
    roster_builder.write_roster_file(Path(args.file), block)

    # ONLY NOW mark them listed. A creator flipped to `listed` before the roster reaches the
    # registry would have the service sign and publish their bundles against a key no installed
    # client trusts yet — every download of theirs would fail verification, fail-closed, with
    # nothing to point at.
    for creator in waiting:
        directory.admit(creator.id)

    print(f"\nroster updated: {len(block.get('roster') or [])} creator(s) — wrote {args.file}")
    print("NOW PUBLISH IT, or their bundles still will not verify:")
    print(f"    agentd bundle roster publish --file {args.file} --to <registry>")
    return 0


def run_roster_revoke(args: argparse.Namespace) -> int:
    from agent_runtime.domain.bundle import BundleError
    from agent_runtime.infrastructure.marketplace import roster_builder

    try:
        root_private, root_public = _read_keypair(args.root_key)
        existing = _load_roster(args.file)
        if not existing:
            print(f"no roster at {args.file} — nothing to revoke")
            return 1
        block = roster_builder.without_publisher(
            existing, publisher_id=args.id, issued=_now_iso(),
            root_private_b64=root_private, root_public_b64=root_public,
        )
    except (OSError, ValueError, KeyError, BundleError) as e:
        print(f"cannot update the roster: {e}")
        return 1
    roster_builder.write_roster_file(Path(args.file), block)
    print(f"revoked {args.id}. Their row is kept for the record; their key no longer verifies.")
    print(
        "This takes effect for a client on its next index fetch — bundles ALREADY installed from "
        "them stay installed. Revocation stops new installs; it is not a remote uninstall."
    )
    return 0


def run_roster_show(args: argparse.Namespace) -> int:
    from agent_runtime.domain.bundle import BundleError, parse_publisher_roster
    from agent_runtime.infrastructure.marketplace.trust import verify_roster

    try:
        block = _load_roster(args.file)
    except BundleError as e:
        print(e)
        return 1
    if not block:
        print(f"no roster at {args.file}")
        return 1
    roster = parse_publisher_roster(block)
    print(f"roster issued {roster.issued or '(no date)'} — {len(roster.entries)} creator(s)")
    revoked = set(roster.revoked)
    for entry in roster.entries:
        mark = "REVOKED" if entry.id in revoked else "trusted"
        print(f"  {mark:8} {entry.id:20} {entry.key[:16]}…  {entry.name}")
    root_key = str(block.get("root_key") or "")
    if args.root_key:
        root_key = args.root_key
        if Path(root_key).is_file():
            root_key = _read_keypair(root_key)[1]
    if not root_key:
        print("\nno root key to check the signature against (pass --root-key)")
        return 0
    ok = verify_roster(roster, root_key)
    print(f"\nsignature: {'VALID' if ok else 'INVALID'} against {root_key[:16]}…")
    return 0 if ok else 1


def run_roster_publish(args: argparse.Namespace) -> int:
    """Replace a registry's roster in place. No bundles are packed and none are touched."""
    import json
    import os
    import tempfile

    from agent_runtime.domain.bundle import BundleError, parse_publisher_roster

    target = (args.to or os.environ.get("AGENTD_PUBLISH_TARGET", "")).strip()
    if not target:
        print("no registry target. Pass --to <directory|s3://bucket[/prefix]>.")
        return 1
    is_s3 = target.startswith(_S3_SCHEME)

    try:
        block = _load_roster(args.file)
    except BundleError as e:
        print(e)
        return 1
    if not block:
        print(f"no roster at {args.file}")
        return 1

    existing = _read_registry_index(target, is_s3)
    if existing is None:
        return 1
    if not existing:
        print("there is no registry at that target yet — publish a bundle first.")
        return 1

    # A roster older than the one already served would be REFUSED by every client as a replay, and
    # the registry would be stuck that way until someone noticed. Catch it here instead.
    prior = str((existing.get("publishers") or {}).get("issued") or "")
    issued = str(block.get("issued") or "")
    if prior and issued and issued < prior:
        print(
            f"refusing: this roster ({issued}) is OLDER than the one the registry already serves "
            f"({prior}). Clients reject a roster that goes backwards — publishing it would break "
            "the store for everyone who has already seen the newer one."
        )
        return 1

    roster = parse_publisher_roster(block)
    trusted = roster.trusted_keys()
    orphaned = sorted(
        {
            str(b.get("publisher_id") or "")
            for b in (existing.get("bundles") or [])
            if b.get("publisher_id") and str(b.get("publisher_id")) not in trusted
        }
    )
    index = dict(existing)
    index["schema"] = 2
    index["publishers"] = block
    index["publisher_key"] = str(block.get("root_key") or "") or str(existing.get("publisher_key") or "")

    print(f"roster: {len(trusted)} trusted creator(s), {len(roster.revoked)} revoked")
    if orphaned:
        # Not an error — revoking someone is SUPPOSED to stop their bundles installing. It is
        # printed because the listing still shows those cards, so the store looks unchanged and
        # only the download fails, which is a confusing thing to discover from a user report.
        print(
            "  these bundles will stop installing (their creator is revoked or unlisted): "
            + ", ".join(
                f"{b.get('id')} [{b.get('publisher_id')}]"
                for b in (existing.get("bundles") or [])
                if str(b.get("publisher_id") or "") in orphaned
            )
        )
    if args.dry_run:
        print(f"\n--dry-run: nothing uploaded. Would update the roster at {target}")
        return 0

    with tempfile.TemporaryDirectory(prefix="agentd-roster-") as work:
        staging = Path(work)
        (staging / "index.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
        # No .agentpkg in staging, so this uploads index.json and nothing else — the artifacts
        # already in the registry are untouched.
        if _upload_registry(staging, target, is_s3) != 0:
            print("upload FAILED — the registry's roster is unchanged.")
            return 1
    print("\nroster published. Clients pick it up on their next index fetch.")
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

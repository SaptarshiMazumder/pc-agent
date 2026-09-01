"""`agentd license ...` — offline license management (M7).

  issue   (publisher tooling) sign a license file granting SKU(s)
  add     drop a received .lic file into this install's licenses folder
  list    show the licenses this install has and which SKUs verify

Verification needs the publisher key pinned in distribution.toml; `list` says so
explicitly when it isn't (the open build has nothing to verify against)."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


def register(subparsers: argparse._SubParsersAction) -> None:
    license_parser = subparsers.add_parser("license", help="licenses: issue / add / list")
    sub = license_parser.add_subparsers(dest="license_command", required=True)

    issue = sub.add_parser("issue", help="(publisher) sign a license file")
    issue.add_argument("--key", required=True, help="keypair file from `agentd bundle keygen`")
    issue.add_argument("--sku", required=True, help="comma-separated SKU(s) to grant")
    issue.add_argument(
        "--expires", default="", help="ISO date (e.g. 2027-07-04); empty = perpetual"
    )
    issue.add_argument("--subject", default="", help="who this license is for (email/customer id)")
    issue.add_argument("--out", default="", help="output .lic path (default <first-sku>.lic)")
    issue.set_defaults(func=run_issue)

    add = sub.add_parser("add", help="install a received .lic file")
    add.add_argument("file")
    add.set_defaults(func=run_add)

    list_parser = sub.add_parser("list", help="show licenses + verified SKUs")
    list_parser.set_defaults(func=run_list)


def run_issue(args: argparse.Namespace) -> int:
    from agent_runtime.infrastructure.licensing import issue_license

    keypair = json.loads(Path(args.key).read_text(encoding="utf-8"))
    skus = [s.strip() for s in args.sku.split(",") if s.strip()]
    content = issue_license(
        keypair["private_key"], skus, expires=args.expires, subject=args.subject
    )
    out = Path(args.out or f"{skus[0]}.lic")
    out.write_text(content, encoding="utf-8")
    print(
        f"license written: {out}  (skus: {', '.join(skus)}"
        + (f", expires {args.expires})" if args.expires else ", perpetual)")
    )
    return 0


def run_add(args: argparse.Namespace) -> int:
    from agent_runtime import runtime_paths

    source = Path(args.file)
    if not source.is_file():
        print(f"no such file: {source}")
        return 1
    licenses_dir = runtime_paths.licenses_dir()
    licenses_dir.mkdir(parents=True, exist_ok=True)
    dest = licenses_dir / source.name
    shutil.copyfile(source, dest)
    print(f"added {dest} — restart the daemon (`agentd restart`) to apply")
    return run_list(args)


def run_list(args: argparse.Namespace) -> int:
    from agent_runtime import runtime_paths
    from agent_runtime.config import load_config
    from agent_runtime.infrastructure.licensing import entitled_skus, load_licenses

    config = load_config()
    publisher_key = getattr(config.distribution, "publisher_key", "")
    licenses_dir = runtime_paths.licenses_dir()
    files = sorted(licenses_dir.glob("*.lic")) if licenses_dir.is_dir() else []
    if not publisher_key:
        print(f"{len(files)} license file(s) in {licenses_dir}")
        print("this install pins NO publisher key (open build) — licenses are not enforced")
        return 0
    licenses = load_licenses(licenses_dir, publisher_key)
    for lic in licenses:
        print(
            f"  {Path(lic.path).name:<32} skus: {', '.join(lic.skus)}"
            + (f"  (until {lic.expires})" if lic.expires else "  (perpetual)")
        )
    invalid = len(files) - len(licenses)
    if invalid:
        print(f"  ({invalid} file(s) failed verification — see the daemon log)")
    print(f"\nverified SKUs: {', '.join(sorted(entitled_skus(licenses))) or '(none)'}")
    return 0

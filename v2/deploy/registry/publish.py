"""Build the PUBLIC marketplace registry — pack agents, sign the index, hand off the upload.

One command turns a list of agent dirs into a complete, signed static registry (a folder of
.agentpkg files + index.json) that any static HTTPS host serves as-is (S3, a CDN, or
`agentd bundle serve` for local testing — RegistryClient verifies sha256 + the ed25519
signature client-side, so the host needs no smarts).

    python deploy/registry/publish.py --key <keypair.json> [--out dist/registry]
                                      [--agents figure-creator,presentation-creator]
    python deploy/registry/publish.py --key <keypair.json> --serve          # local test

Everything is parameterized (args > env > defaults); nothing points at a fixed bucket:
    AGENTD_PUBLISH_AGENTS   default agent list (comma-separated ids under v2/agents)
    AGENTD_PUBLISH_OUT      default output directory
    AGENTD_PUBLISH_BUCKET   S3 bucket name — only used to PRINT the exact upload command
    AGENTD_PUBLISHER_KEY    default keypair file (from `agentd bundle keygen`; keep it
                            OUTSIDE the repo — the private key never ships or commits)

The upload itself is printed, not executed (the operator runs all aws commands by hand).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

_here = Path(__file__).resolve()
_v2 = _here.parents[2]  # …/v2

_DEFAULT_AGENTS = "figure-creator,presentation-creator"
_REGISTRY_NAME = "agentd marketplace"
_PUBLISHER = "agentd"


# The CLI entry (agentd.cli.main), NOT `-m agentd` — the bare module entry boots the DAEMON.
_CLI = [sys.executable, "-c", "from agentd.cli.main import main; raise SystemExit(main())"]


def _run(cli_args: list[str]) -> None:
    print(f"$ agentd {' '.join(cli_args)}")
    result = subprocess.run(_CLI + cli_args, cwd=str(_v2))
    if result.returncode != 0:
        sys.exit(result.returncode)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--agents",
        default=os.environ.get("AGENTD_PUBLISH_AGENTS", _DEFAULT_AGENTS),
        help=f"comma-separated agent ids under v2/agents (default: {_DEFAULT_AGENTS})",
    )
    parser.add_argument(
        "--out",
        default=os.environ.get("AGENTD_PUBLISH_OUT", str(_v2 / "dist" / "registry")),
        help="registry output directory (default v2/dist/registry)",
    )
    parser.add_argument(
        "--key",
        default=os.environ.get("AGENTD_PUBLISHER_KEY", ""),
        help="publisher keypair file from `agentd bundle keygen` (omitting it builds an "
        "UNSIGNED index — fine locally, never for the public registry)",
    )
    parser.add_argument(
        "--bucket",
        default=os.environ.get("AGENTD_PUBLISH_BUCKET", ""),
        help="S3 bucket name; used only to print the upload command",
    )
    parser.add_argument(
        "--serve", action="store_true", help="serve the built registry locally (test mode)"
    )
    args = parser.parse_args()

    agents = [a.strip() for a in args.agents.split(",") if a.strip()]
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    for agent in agents:
        agent_dir = _v2 / "agents" / agent
        if not (agent_dir / "bundle.toml").is_file():
            sys.exit(f"no bundle.toml in {agent_dir} — every published agent declares one")
        _run(["bundle", "pack", str(agent_dir), "--out", str(out)])

    index_cmd = ["bundle", "index", str(out), "--name", _REGISTRY_NAME, "--publisher", _PUBLISHER]
    if args.key:
        index_cmd += ["--key", args.key]
    else:
        print("WARNING: no --key / AGENTD_PUBLISHER_KEY — building an UNSIGNED index")
    _run(index_cmd)

    print(f"\nregistry built: {out}")
    if args.serve:
        _run(["bundle", "serve", str(out)])
        return
    bucket = args.bucket or "<your-registry-bucket>"
    print("\nTo publish (run yourself):")
    print(
        f"  aws s3 sync \"{out}\" s3://{bucket}/ "
        '--exclude "*" --include "*.agentpkg" --include "index.json"'
    )
    print(f"  registry_url for flavors: https://{bucket}.s3.<region>.amazonaws.com/index.json")


if __name__ == "__main__":
    main()

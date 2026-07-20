#!/bin/sh
# agentd container boot: install/refresh the baked curated bundles into the state volume,
# then run the daemon in the foreground. Idempotent — a bundle already recorded in the
# installed ledger at the same version is skipped, so restarts are instant and an image
# carrying a NEWER bundle upgrades the agent on its first boot.
set -e

mkdir -p "$AGENTD_HOME"
# one daemon per container: a stale rendezvous file from the previous run must not trip
# the single-instance guard (lifecycle.find_running) after a restart.
rm -f "$AGENTD_HOME/gateway.json"

for pkg in /opt/agentd/bundles/*.agentpkg; do
  [ -e "$pkg" ] || continue
  if python - "$pkg" <<'PY'
import json, os, sys
from pathlib import Path

from agentd.infrastructure.marketplace.bundle_io import read_manifest

# exit 0 = ledger already records this bundle at this version (skip); 1 = install
try:
    m = read_manifest(Path(sys.argv[1]))
    home = Path(os.environ.get("AGENTD_HOME") or (Path.home() / ".agentd"))
    ledger = json.loads((home / "state" / "installed_bundles.json").read_text("utf-8"))
    up_to_date = any(
        r.get("id") == m.id and r.get("version") == m.version
        for r in ledger.get("bundles", [])
    )
except Exception:
    up_to_date = False
sys.exit(0 if up_to_date else 1)
PY
  then
    echo "bundle up to date: $pkg"
  else
    echo "installing bundle: $pkg"
    # offline path: no daemon is running yet, the CLI installs in-process
    agentd install "$pkg"
  fi
done

exec agentd serve

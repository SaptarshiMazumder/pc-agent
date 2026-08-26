#!/usr/bin/env bash
# Build the agentd Setup exe, END TO END, in the order the pieces feed each other:
#
#   1. bump the version   (agent_runtime/__init__.py + package.json -- the installer's name)
#   2. SDK                (clients/sdk-js dist -- what every agent window runs on)
#   3. vendor             (copy that SDK into the skeleton + every existing agent)
#   4. Agent Builder UI   (agents/agent-builder/ui -- bundles the SDK + shared auth card)
#   5. runtime            (build-runtime.ps1: the agentd wheel inside the bundled python,
#                          chromium / node / app-deps are cached across runs)
#   6. dist:core          (electron-vite + electron-builder -> the Setup exe)
#
# Skipping 2-4 is how a "fresh" exe ships a stale SDK: the wheel bakes in whatever is on disk,
# and nothing else checks. This script exists so the chain cannot be half-run.
#
#   ./scripts/build-exe.sh                # bump patch (0.1.12 -> 0.1.13), build
#   ./scripts/build-exe.sh --no-bump      # keep the current version
#   ./scripts/build-exe.sh --version 0.2.0

set -euo pipefail

DESKTOP="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # clients/desktop
V2="$(cd "$DESKTOP/../.." && pwd)"

VERSION=""
NO_BUMP=0
while [ $# -gt 0 ]; do
  case "$1" in
    --version) VERSION="${2:?--version needs a value}"; shift 2 ;;
    --no-bump) NO_BUMP=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

step() { printf '\n=== %s ===\n' "$1"; }

# --- 1. the version, stamped in BOTH places or neither -------------------------------------
# package.json names the installer; __init__.py is what the daemon reports. When they drift,
# "agentd Setup X.exe" ships a daemon that says Y and every bug report starts with a lie.
PKG="$DESKTOP/package.json"
INIT="$V2/agent_runtime/__init__.py"
CUR="$(sed -n 's/.*"version": "\([0-9]*\.[0-9]*\.[0-9]*\)".*/\1/p' "$PKG" | head -1)"
[ -n "$CUR" ] || { echo "no semver version in $PKG" >&2; exit 1; }
if [ -n "$VERSION" ]; then
  NEW="$VERSION"
elif [ "$NO_BUMP" = 1 ]; then
  NEW="$CUR"
else
  NEW="${CUR%.*}.$(( ${CUR##*.} + 1 ))"
fi
if [ "$NEW" != "$CUR" ]; then
  grep -q "__version__ = \"$CUR\"" "$INIT" || {
    echo "agent_runtime/__init__.py does not say $CUR - the two versions have already drifted; fix that first" >&2
    exit 1
  }
  sed -i "s/\"version\": \"$CUR\"/\"version\": \"$NEW\"/" "$PKG"
  sed -i "s/__version__ = \"$CUR\"/__version__ = \"$NEW\"/" "$INIT"
fi
echo "version: $CUR -> $NEW"

# --- 2 + 3. the SDK, then vendored everywhere that carries a copy --------------------------
step "SDK (clients/sdk-js)"
( cd "$V2/clients/sdk-js" && npm run build && node scripts/vendor.mjs )

# --- 4. Agent Builder's own window ---------------------------------------------------------
step "Agent Builder UI"
( cd "$V2/agents/agent-builder/app" && npm run build )

# --- 5. the embedded runtime (wheel rebuilt every time; big downloads cached) --------------
step "runtime (build-runtime.ps1)"
( cd "$DESKTOP" && powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build-runtime.ps1 )

# --- 6. the installer ----------------------------------------------------------------------
step "installer (dist:core)"
( cd "$DESKTOP" && npm run dist:core )

# The artifact itself, or this script has no business reporting success.
EXE="$DESKTOP/dist/core/agentd Setup $NEW.exe"
[ -f "$EXE" ] || { echo "dist:core reported success but '$EXE' does not exist" >&2; exit 1; }
MB=$(( $(stat -c%s "$EXE") / 1024 / 1024 ))
printf '\ninstaller ready: %s (%s MB)\n' "$EXE" "$MB"
echo "before installing over a RUNNING setup: node scripts/kill-daemon.mjs (the old daemon survives the installer)"

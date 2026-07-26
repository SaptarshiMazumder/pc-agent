#!/usr/bin/env bash
# Build the agentd Windows installer LOCALLY — same 3 steps as the release workflow
# (.github/workflows/release-build.yml), minus the tag/version gate:
#
#   1. wheel      v2 package -> dist/agentd-*.whl         (built with v2/.venv python)
#   2. runtime    scripts/build-runtime.ps1 -Wheel ...    (relocatable CPython + wheel;
#                 stays PowerShell — it's the repo's own script, invoked from here)
#   3. installer  npm run dist:core                       (electron-vite + NSIS)
#
# Run (Git Bash, from anywhere):
#   bash v2/clients/desktop/scripts/build-installer-local.sh [--skip-runtime]
#
# --skip-runtime reuses the cached runtime/cpython (UI-only rebuilds).
# Output: dist/core/agentd Setup <version>.exe
# First run downloads ~30 MB of CPython (cached in runtime/) and NSIS tooling.

set -euo pipefail

DESKTOP="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # clients/desktop
V2="$(cd "$DESKTOP/../.." && pwd)"                            # v2/
VENV_PY="$V2/.venv/Scripts/python.exe"

SKIP_RUNTIME=0
[[ "${1:-}" == "--skip-runtime" ]] && SKIP_RUNTIME=1

if (( ! SKIP_RUNTIME )); then
  # -- 1. the wheel, built with the venv's 3.13 (bare `python` may be a 3.10 that
  #       cannot even import the package; the wheel itself is pure py3-none-any) --
  [[ -x "$VENV_PY" ]] || { echo "ERROR: no venv at $VENV_PY — create it first (see v2/README.md)"; exit 1; }
  echo "== [1/3] building the agentd wheel =="
  "$VENV_PY" -m pip install --quiet build
  (cd "$V2" && "$VENV_PY" -m build --wheel --outdir "$V2/dist")
  WHEEL="$(ls -t "$V2"/dist/agentd-*.whl | head -1)"
  WHEEL_WIN="$(cygpath -w "$WHEEL")"                          # PS needs a Windows path
  echo "wheel: $WHEEL_WIN"

  # -- 2. the embedded daemon runtime (relocatable CPython with the wheel installed) --
  echo "== [2/3] assembling the embedded runtime =="
  (cd "$DESKTOP" && powershell -ExecutionPolicy Bypass -File scripts/build-runtime.ps1 -Wheel "$WHEEL_WIN")
else
  [[ -f "$DESKTOP/runtime/cpython/python.exe" ]] \
    || { echo "ERROR: --skip-runtime given but runtime/cpython does not exist yet — run once without it"; exit 1; }
  echo "== [1-2/3] skipped (reusing runtime/cpython) =="
fi

# -- 3. the NSIS installer (core flavor: flavors/core/distribution.toml baked in) --
echo "== [3/3] building the installer (npm run dist:core) =="
(cd "$DESKTOP" && npm run dist:core)

EXE="$(ls -t "$DESKTOP"/dist/core/*.exe | head -1)"
echo
echo "DONE -> $(cygpath -w "$EXE")"
echo "Install it, launch 'agentd' from the Start menu — the app spawns its own daemon"
echo "from the embedded runtime (no Python needed on the machine)."

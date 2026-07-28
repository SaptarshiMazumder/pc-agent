#!/usr/bin/env bash
# Build a per-AGENT installer — one shippable .exe per agent, each booting straight into
# that agent's own UI. Sibling of build-installer-local.sh (which builds the CORE/JARVIS
# client); same 3 stages, only the last one differs:
#
#   1. wheel      v2 package -> dist/agentd-*.whl        (built with v2/.venv python)
#   2. runtime    scripts/build-runtime.ps1 -Wheel ...   (relocatable CPython + wheel)
#   3. installer  per agent: npm run gen:app  -> dist/app-flavors/<id>/  (flavor + .agentpkg)
#                            npm run dist:app -> agents/<id>/clients/desktop/<Name> Setup.exe
#
# Stages 1-2 run ONCE and are shared by every agent in the run — only stage 3 loops.
#
# Run (Git Bash, from anywhere):
#   bash v2/clients/desktop/scripts/build-agent-installer.sh expense-summarizer
#   bash v2/clients/desktop/scripts/build-agent-installer.sh expense-summarizer weather
#   bash v2/clients/desktop/scripts/build-agent-installer.sh --all [--skip-runtime]
#
# --all         every agent under v2/agents/ whose agent.toml declares [app]; the rest are
#               skipped with a printed reason (only app agents can become products).
# --skip-runtime  reuse the cached runtime/cpython (skips stages 1-2 entirely).
#
# This script is ORCHESTRATION ONLY. Packing lives in `agentd bundle pack`
# (agentd/infrastructure/marketplace/bundle_io.py), branding in gen-app-flavor.mjs,
# packaging in electron-builder. Nothing here duplicates them.
#
# Installing several of these on one machine is additive: each exe is its own Windows
# product (appId dev.agentd.app.<id>) but they all share ~/.agentd/agents + ONE daemon,
# so agent #2 never removes agent #1.
#
# NOTE: stage 3 calls `npm run dist:app`, which re-runs `electron-vite build` per agent.
# The renderer output is identical every time — hoisting it is a future optimization that
# would mean bypassing dist-app.mjs (and duplicating its delivery logic), so it is left
# alone deliberately.

set -euo pipefail

DESKTOP="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # clients/desktop
V2="$(cd "$DESKTOP/../.." && pwd)"                            # v2/
VENV_PY="$V2/.venv/Scripts/python.exe"
AGENTS_DIR="$V2/agents"

# -- args ---------------------------------------------------------------------------
SKIP_RUNTIME=0
ALL=0
AGENT_IDS=()
for arg in "$@"; do
  case "$arg" in
    --skip-runtime) SKIP_RUNTIME=1 ;;
    --all)          ALL=1 ;;
    -*)             echo "ERROR: unknown flag '$arg'"; exit 1 ;;
    *)              AGENT_IDS+=("$arg") ;;
  esac
done

if (( ALL )) && (( ${#AGENT_IDS[@]} )); then
  echo "ERROR: --all takes no agent ids (got: ${AGENT_IDS[*]})"; exit 1
fi
if (( ! ALL )) && (( ! ${#AGENT_IDS[@]} )); then
  echo "usage: build-agent-installer.sh <agent-id>... | --all  [--skip-runtime]"; exit 1
fi

# an APP agent = agents/<id>/agent.toml with an [app] section (gen-app-flavor rejects the rest)
is_app_agent() { grep -qE '^\s*\[app\]' "$AGENTS_DIR/$1/agent.toml" 2>/dev/null; }

if (( ALL )); then
  echo "== discovering app agents in $AGENTS_DIR =="
  for dir in "$AGENTS_DIR"/*/; do
    id="$(basename "$dir")"
    if [[ ! -f "$dir/agent.toml" ]]; then
      echo "  skip $id — no agent.toml"
    elif ! is_app_agent "$id"; then
      echo "  skip $id — no [app] section (not an app agent)"
    else
      echo "  build $id"
      AGENT_IDS+=("$id")
    fi
  done
  (( ${#AGENT_IDS[@]} )) || { echo "no app agents found — nothing to build"; exit 1; }
else
  # explicit ids: fail early on a typo rather than 20 minutes into a runtime build
  for id in "${AGENT_IDS[@]}"; do
    [[ -f "$AGENTS_DIR/$id/agent.toml" ]] || { echo "ERROR: no agent at $AGENTS_DIR/$id"; exit 1; }
    is_app_agent "$id" || { echo "ERROR: '$id' has no [app] section — only app agents can become products"; exit 1; }
  done
fi
echo

# -- stages 1-2: wheel + embedded runtime (ONCE, shared by every agent) ---------------
if (( ! SKIP_RUNTIME )); then
  [[ -x "$VENV_PY" ]] || { echo "ERROR: no venv at $VENV_PY — create it first (see v2/README.md)"; exit 1; }
  echo "== [1/3] building the agentd wheel =="
  "$VENV_PY" -m pip install --quiet build
  (cd "$V2" && "$VENV_PY" -m build --wheel --outdir "$V2/dist")
  WHEEL="$(ls -t "$V2"/dist/agentd-*.whl | head -1)"
  WHEEL_WIN="$(cygpath -w "$WHEEL")"                          # PS needs a Windows path
  echo "wheel: $WHEEL_WIN"

  echo "== [2/3] assembling the embedded runtime =="
  (cd "$DESKTOP" && powershell -ExecutionPolicy Bypass -File scripts/build-runtime.ps1 -Wheel "$WHEEL_WIN")
else
  [[ -f "$DESKTOP/runtime/cpython/python.exe" ]] \
    || { echo "ERROR: --skip-runtime given but runtime/cpython does not exist yet — run once without it"; exit 1; }
  echo "== [1-2/3] skipped (reusing runtime/cpython) =="
fi
echo

# -- stage 3: one installer per agent -------------------------------------------------
# A failing agent does NOT abort the run (a bad agent shouldn't cost you the other 8);
# failures are collected and reported, and the script exits non-zero at the end.
BUILT=()
FAILED=()
total=${#AGENT_IDS[@]}
n=0
for id in "${AGENT_IDS[@]}"; do
  n=$(( n + 1 ))
  echo "== [3/3] ($n/$total) $id =="
  if (cd "$DESKTOP" && npm run gen:app -- "$id" && npm run dist:app -- "$id"); then
    BUILT+=("$id")
  else
    echo "!! $id FAILED — continuing with the rest"
    FAILED+=("$id")
  fi
  echo
done

# -- summary --------------------------------------------------------------------------
echo "================ summary ================"
for id in "${BUILT[@]:-}"; do
  [[ -n "$id" ]] || continue
  exe="$(ls -t "$AGENTS_DIR/$id"/clients/desktop/*.exe 2>/dev/null | head -1 || true)"
  echo "  OK      $id -> ${exe:-(exe not found?)}"
done
for id in "${FAILED[@]:-}"; do
  [[ -n "$id" ]] || continue
  echo "  FAILED  $id"
done
echo "========================================="
echo "built ${#BUILT[@]}/$total"

if (( ${#FAILED[@]} )); then
  exit 1
fi
echo
echo "Each installer is standalone: it ships the client + embedded daemon and installs"
echo "its own agent into ~/.agentd/agents/ on first launch, leaving other agents intact."

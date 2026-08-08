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
REPO="$(cd "$V2/.." && pwd)"                                  # pc-agent/
AGENTS_DIR="$V2/agents"

# The venv is DISCOVERED, not assumed. It has lived at both v2/.venv and the repo root, and
# hardcoding one meant this script died with "no venv" on a working checkout while every other
# tool (gen-app-flavor, pytest) found it fine.
VENV_PY=""
for candidate in "$V2/.venv/Scripts/python.exe" "$REPO/.venv/Scripts/python.exe" \
                 "$V2/.venv/bin/python" "$REPO/.venv/bin/python"; do
  if [[ -x "$candidate" ]]; then VENV_PY="$candidate"; break; fi
done

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
  [[ -n "$VENV_PY" ]] || {
    echo "ERROR: no venv found (looked in $V2/.venv and $REPO/.venv) — create it first (see v2/README.md)"
    exit 1
  }
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

  # STALENESS GUARD. --skip-runtime reuses whatever agentd was baked into runtime/cpython, and
  # nothing about the resulting installer looks wrong: it builds, installs, and launches. The
  # daemon then dies on the FIRST import that changed since the runtime was assembled.
  #
  # That is exactly what shipped once: a runtime built 2026-07-24 was reused after the
  # agentd -> agent_runtime package rename on 07-30, so a freshly downloaded installer crashed
  # with `ModuleNotFoundError: No module named 'agentd'` — in code the repo had fixed weeks
  # earlier. A stale runtime cannot be caught by a version check either; the version was 0.1.5
  # on both sides. mtime is the only honest signal.
  #
  # REFUSES rather than warns: the flag exists to save minutes, and the failure it hides is a
  # broken installer published to real users. A warning in a hundred lines of build output is
  # not a defence. The check is precise (it fires only when source really is newer), so it
  # stays quiet on the correct case.
  RUNTIME_MARK="$DESKTOP/runtime/cpython/Lib/site-packages/agent_runtime/__init__.py"
  if [[ -f "$RUNTIME_MARK" ]]; then
    NEWER=$(find "$V2/agent_runtime" "$V2/plugins" -name '*.py' -newer "$RUNTIME_MARK" -print -quit 2>/dev/null || true)
    if [[ -n "$NEWER" ]]; then
      echo "ERROR: --skip-runtime, but runtime/cpython is OLDER than your Python source."
      echo "  runtime baked: $(date -r "$RUNTIME_MARK" '+%Y-%m-%d %H:%M' 2>/dev/null || echo '?')"
      echo "  newer source : ${NEWER#$V2/}"
      echo
      echo "Reusing it would ship a daemon that crashes on the first changed import, in an"
      echo "installer that otherwise looks fine. Re-run WITHOUT --skip-runtime to rebuild it."
      exit 1
    fi
  else
    echo "ERROR: runtime/cpython has no agent_runtime installed (pre-rename or broken build)."
    echo "Re-run WITHOUT --skip-runtime."
    exit 1
  fi
  echo "== [1-2/3] skipped (runtime/cpython is newer than your source) =="
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

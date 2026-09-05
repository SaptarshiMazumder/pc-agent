# Stage-1 gate - run locally; mirrors exactly what .github/workflows/ci-fast-tests.yml runs in the cloud.
# Auto-uses the repo's root .venv (no need to activate it first).
# Blocking gates stop the script on the first failure. mypy is informational (not a gate yet).
#
#   pwsh v2/scripts/verify.ps1        (or: powershell -File v2/scripts/verify.ps1)
#
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads BOM-less .ps1 as ANSI,
# so non-ASCII characters (em-dashes, smart quotes) break the parser.

$ErrorActionPreference = "Stop"
$repo = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location (Join-Path $PSScriptRoot "..")           # -> v2/

# Prefer the repo venv so this works whether or not it's activated.
$py = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
$linter = Join-Path $repo ".venv\Scripts\lint-imports.exe"
if (-not (Test-Path $linter)) { $linter = "lint-imports" }

function Invoke-Gate($name, $script) {
    Write-Host "== $name ==" -ForegroundColor Cyan
    & $script
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAILED: $name (exit $LASTEXITCODE)" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

# --- blocking gates (a red one stops a merge in CI) ---
Invoke-Gate "ruff check"          { & $py -m ruff check . }
Invoke-Gate "ruff format --check" { & $py -m ruff format --check . }
Invoke-Gate "import-linter"       { & $linter }
Invoke-Gate "pytest (unit)"        { & $py -m pytest tests/unit -q }
Invoke-Gate "pytest (integration)" { & $py -m pytest tests/integration -m "not live and not browser and not computer" -q }
# model-proxy ships its own pins (litellm[proxy]) and runs as its own CI job. Needs a one-time
#   pip install -r model-proxy/requirements.txt pytest pytest-asyncio
# in whichever interpreter this script picks up; model-proxy/pytest.ini drives the run.
Invoke-Gate "pytest (model-proxy)"  { & $py -m pytest model-proxy -q }
# tests/e2e (booted-daemon smoke) is a reserved tier with no tests yet (the dir doesn't exist);
# the agent-diagnosis harness that used to sit there lives in agent_runtime/e2e/ and is not pytest.

# --- informational (does NOT block; burn the count down, then promote to a gate) ---
Write-Host "== mypy (informational) ==" -ForegroundColor Cyan
$mypy = & $py -m mypy 2>&1
$mypy | Select-Object -Last 1
Write-Host "  (informational, not a blocking gate yet)" -ForegroundColor DarkGray

Write-Host "`nAll Stage-1 blocking gates passed." -ForegroundColor Green
exit 0

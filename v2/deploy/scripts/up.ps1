# =============================================================================
# up.ps1 - bring an environment back. Reverse of down.ps1, either kind.
#
#   ./up.ps1                       # dev (default)
#   ./up.ps1 -Environment staging
#
# A ONE-LINE WRAPPER around `terraform apply` (paused and hibernate both default to false), which
# you can run directly instead. Kept so existing habits and notes keep working.
#
# It no longer needs to know WHICH kind of down you did. Both are Terraform variables now, so a
# plain apply is the way back from either: it scales tasks up, re-enables the scheduled jobs, and
# recreates the ALB and services if they were removed. The old version had to detect a missing
# ALB by grepping `terraform state list`, and had a hand-typed task list that had already gone
# stale (it still named four services after `ingest` became the fifth).
#
# Data (EFS, secrets, ECR images) is never removed by either script.
# =============================================================================
param([string]$Environment = "dev")

$ErrorActionPreference = "Stop"
$v2     = (Resolve-Path "$PSScriptRoot/../..").Path   # deploy/scripts -> deploy -> v2
$envDir = Join-Path $v2 "infra/environments/$Environment"
if (-not (Test-Path $envDir)) { throw "No such environment: $envDir" }

$chdir = "-chdir=$envDir"   # quoted so PowerShell does not mangle the native argument

# Was the ALB gone? Ask BEFORE the apply — afterwards it exists either way, and the answer
# decides whether the web image now has a stale URL baked into it.
$wasHibernated = -not (terraform $chdir state list 2>$null | Select-String -Quiet '^module\.stack\.aws_lb\.main\[0\]$')

Write-Host "== Resuming $Environment - tasks back to their desired counts, schedules on ==" -ForegroundColor Cyan
terraform $chdir apply -auto-approve
if ($LASTEXITCODE -ne 0) { throw "terraform apply failed (exit $LASTEXITCODE)." }

Write-Host "== Up. Tasks starting (~1-2 min to healthy). App URL: ==" -ForegroundColor Green
terraform $chdir output -raw app_url
Write-Host ""

if ($wasHibernated) {
  Write-Host "   The ALB was recreated, so this is a NEW hostname." -ForegroundColor Yellow
  Write-Host "   * desktop: nothing to do - `npm run dev` re-syncs the flavors automatically." -ForegroundColor Yellow
  Write-Host "   * web:     merge to develop (or run the Deploy workflow) so its image is" -ForegroundColor Yellow
  Write-Host "              rebuilt against this URL - it is baked in at build time." -ForegroundColor Yellow
}

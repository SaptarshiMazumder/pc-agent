# =============================================================================
# down.ps1 - stop paying for an environment. Data is never touched.
#
#   ./down.ps1                       # pause:     tasks to 0, ALB KEPT   (~$21/mo, URL stable)
#   ./down.ps1 -Full                 # hibernate: ALB gone too           (~$3/mo,  URL CHANGES)
#   ./down.ps1 -Environment staging
#
# BOTH ARE ONE-LINE WRAPPERS around `terraform apply`, which you can run directly:
#   terraform -chdir=v2/infra/environments/dev apply -var paused=true
#   terraform -chdir=v2/infra/environments/dev apply -var hibernate=true
#
# WHAT CHANGED, AND WHY. This script used to scale a HAND-TYPED list of services with the AWS
# CLI - "model-proxy", "accounts", "daemon", "web" - and that list had already drifted: `ingest`
# became the fifth service and this kept scaling four, so every "down" quietly left one running
# and every "up" never brought it back. Terraform iterates the services map, so it cannot be
# wrong again. It also means Terraform's state now MATCHES reality: previously `desired_count`
# was owned by this script and ignored by Terraform, so the only way to know whether an
# environment was paused was to ask AWS.
#
# WHAT -Full COSTS, STATED PLAINLY. An ALB's DNS name carries an AWS-assigned suffix, so the one
# you get back is a NEW hostname every time. Consequences, and what handles each:
#   * desktop flavors  -> self-heal: `npm run dev` runs sync-platform-urls as predev
#   * internal traffic -> unaffected: services find each other at *.agentd.local, not via the ALB
#   * the WEB image    -> NEEDS A REBUILD. Its API origins are baked in and pinned in its CSP.
#                         Merge to develop (or run Deploy manually) after `up`.
#   * installed apps   -> orphaned. Fine with no users; not fine later. A domain fixes it.
#
# WHAT NEITHER TOUCHES: EFS (the accounts DB and the ledger), Secrets Manager, ECR images, the
# VPC, the ECS cluster, or the log group.
# =============================================================================
param(
  [string]$Environment = "dev",
  [switch]$Full
)

$ErrorActionPreference = "Stop"
$v2     = (Resolve-Path "$PSScriptRoot/../..").Path   # deploy/scripts -> deploy -> v2
$envDir = Join-Path $v2 "infra/environments/$Environment"
if (-not (Test-Path $envDir)) { throw "No such environment: $envDir" }

# Quoted so PowerShell does not mangle the native argument (same reason the old -target list was).
$chdir = "-chdir=$envDir"

if ($Full) {
  Write-Host "== HIBERNATING $Environment - removing the ALB and the services (~`$3/mo) ==" -ForegroundColor Yellow
  Write-Host "   The public URL WILL be different on the way back up." -ForegroundColor Yellow
  terraform $chdir apply -var "hibernate=true" -auto-approve
} else {
  Write-Host "== PAUSING $Environment - tasks to 0, ALB and URL kept (~`$21/mo) ==" -ForegroundColor Cyan
  terraform $chdir apply -var "paused=true" -auto-approve
}
if ($LASTEXITCODE -ne 0) { throw "terraform apply failed (exit $LASTEXITCODE) - the environment may be partly up." }

Write-Host "== Down. Data intact. Back up with: ./up.ps1 -Environment $Environment ==" -ForegroundColor Green
if ($Full) {
  Write-Host "   After up: merge to develop (or run Deploy) so the web image gets the new URL." -ForegroundColor Yellow
}

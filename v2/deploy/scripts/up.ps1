# =============================================================================
# up.ps1 - bring an environment's stack back up. Reverse of down.ps1.
#
#   ./up.ps1                       # dev (default)
#   ./up.ps1 -Environment staging
#   ./up.ps1 -Environment production
#
# Scales the 4 Fargate tasks back to 1. If the ALB was dropped with `down.ps1 -Full`,
# it recreates it first (terraform apply) and reminds you to re-push the web image
# (the URL changed). Data (EFS, secrets, ECR images) was never removed.
# =============================================================================
param([string]$Environment = "dev")

$ErrorActionPreference = "Stop"
$region   = "ap-northeast-1"
$v2       = (Resolve-Path "$PSScriptRoot/../..").Path            # deploy/scripts -> deploy -> v2
$envDir   = Join-Path $v2 "infra/environments/$Environment"
$chdir    = "-chdir=$envDir"                                     # quoted so PowerShell expands it
$cluster  = "agentd-$Environment"
$services = "gateway", "accounts", "daemon", "web"

if (-not (Test-Path $envDir)) { throw "No such environment: $envDir" }

# Did -Full drop the ALB? Ask Terraform if the ALB is still in state.
$albGone = -not (terraform $chdir state list 2>$null | Select-String -Quiet '^module\.stack\.aws_lb\.main$')

if ($albGone) {
  Write-Host "== ALB missing (was -Full) - recreating it with terraform apply ==" -ForegroundColor Cyan
  terraform $chdir apply -auto-approve
  Write-Host "   ALB recreated. The URL changed -> re-run ./push-images.ps1 -Environment $Environment -Only web." -ForegroundColor Yellow
}

Write-Host "== Scaling Fargate tasks back to 1 ($cluster) ==" -ForegroundColor Cyan
foreach ($s in $services) {
  aws ecs update-service --cluster $cluster --service "$cluster-$s" --desired-count 1 --region $region | Out-Null
  Write-Host "  $cluster-$s -> 1"
}

Write-Host "== Up. Tasks starting (~1-2 min to healthy). App URL: ==" -ForegroundColor Green
terraform $chdir output -raw app_url
Write-Host ""

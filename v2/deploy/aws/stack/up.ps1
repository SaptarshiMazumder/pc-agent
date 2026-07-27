# =============================================================================
# up.ps1 - bring the dev stack back up. Reverse of down.ps1.
#
#   ./up.ps1     scales the 4 Fargate tasks back to 1. If the ALB was dropped with
#                `down.ps1 -Full`, it recreates it first (terraform apply) and reminds
#                you to re-push the web image (the URL changed).
#
# Data (EFS, secrets, ECR images) was never removed, so this is just "turn the tasks on".
# =============================================================================
$ErrorActionPreference = "Stop"
$region   = "ap-northeast-1"
$cluster  = "agentd-dev"
$services = "gateway", "accounts", "daemon", "web"
$chdir    = "-chdir=$PSScriptRoot"   # quoted so PowerShell expands it before terraform sees it

# Did -Full drop the ALB? Ask Terraform if the ALB is still in state.
$albGone = -not (terraform $chdir state list 2>$null | Select-String -Quiet '^aws_lb\.main$')

if ($albGone) {
  Write-Host "== ALB missing (was -Full) - recreating it with terraform apply ==" -ForegroundColor Cyan
  terraform $chdir apply -auto-approve
  Write-Host "   ALB recreated. The URL changed -> re-run ./push-images.ps1 -Only web after this." -ForegroundColor Yellow
}

Write-Host "== Scaling Fargate tasks back to 1 ==" -ForegroundColor Cyan
foreach ($s in $services) {
  aws ecs update-service --cluster $cluster --service "agentd-dev-$s" --desired-count 1 --region $region | Out-Null
  Write-Host "  agentd-dev-$s -> 1"
}

Write-Host "== Up. Tasks starting (~1-2 min to healthy). App URL: ==" -ForegroundColor Green
terraform $chdir output -raw app_url
Write-Host ""

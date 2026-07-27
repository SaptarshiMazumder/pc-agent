# =============================================================================
# down.ps1 - pause the resources that cost money. KEEPS all data + the stable URL.
#
#   ./down.ps1          daily teardown: scale the 4 Fargate tasks to 0 (compute bill -> ~0).
#                       ALB stays up (~$0.60/day) so your app URL does NOT change.
#   ./down.ps1 -Full    longer break: also DESTROY the ALB (saves ~$18/mo). The URL WILL
#                       change on next up, so you must re-push the web image (./push-images.ps1).
#
# Reverse with ./up.ps1. Nothing here deletes EFS, secrets, ECR, or the network - only the
# running tasks (and, with -Full, the load balancer) are touched.
# =============================================================================
param([switch]$Full)

$ErrorActionPreference = "Stop"
$region   = "ap-northeast-1"
$cluster  = "agentd-dev"
$services = "gateway", "accounts", "daemon", "web"
$chdir    = "-chdir=$PSScriptRoot"   # quoted so PowerShell expands it before terraform sees it

Write-Host "== Scaling Fargate tasks to 0 (stops the compute bill) ==" -ForegroundColor Cyan
foreach ($s in $services) {
  aws ecs update-service --cluster $cluster --service "agentd-dev-$s" --desired-count 0 --region $region | Out-Null
  Write-Host "  agentd-dev-$s -> 0"
}

if ($Full) {
  Write-Host "== -Full: destroying the ALB (saves ~18/mo; URL will change on up) ==" -ForegroundColor Yellow
  # -target must be a pre-quoted string (like $chdir); a bare -target=module.alb gets
  # mangled by PowerShell's native-arg parsing and terraform sees a split target.
  $target = "-target=module.alb"
  terraform $chdir destroy $target -auto-approve
  if ($LASTEXITCODE -ne 0) { throw "ALB destroy failed (exit $LASTEXITCODE) - ALB is still up." }
  Write-Host "   ALB gone. On ./up.ps1 you'll get a NEW url -> re-run ./push-images.ps1 -Only web." -ForegroundColor Yellow
} else {
  Write-Host "== ALB left running so the URL stays stable. Use -Full to drop it too. ==" -ForegroundColor Green
}

Write-Host "== Down. Tasks paused, data intact. Morning: ./up.ps1 ==" -ForegroundColor Green

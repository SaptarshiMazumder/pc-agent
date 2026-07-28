# =============================================================================
# down.ps1 - pause the resources that cost money. KEEPS all data + the stable URL.
#
#   ./down.ps1                       # dev daily teardown: scale the 4 tasks to 0 (compute -> ~0).
#   ./down.ps1 -Environment staging  # another environment
#   ./down.ps1 -Full                 # also DESTROY the ALB (saves ~$18/mo). URL changes on up,
#                                    # so you must re-push web (./push-images.ps1 -Only web).
#
# Reverse with ./up.ps1. Nothing here deletes EFS, secrets, ECR, or the network - only the
# running tasks (and, with -Full, the load balancer) are touched.
# =============================================================================
param(
  [string]$Environment = "dev",
  [switch]$Full
)

$ErrorActionPreference = "Stop"
$region   = "ap-northeast-1"
$v2       = (Resolve-Path "$PSScriptRoot/../..").Path            # deploy/scripts -> deploy -> v2
$envDir   = Join-Path $v2 "infra/environments/$Environment"
$chdir    = "-chdir=$envDir"                                     # quoted so PowerShell expands it
$cluster  = "agentd-$Environment"
$services = "model-proxy", "accounts", "daemon", "web"

if (-not (Test-Path $envDir)) { throw "No such environment: $envDir" }

Write-Host "== Scaling Fargate tasks to 0 ($cluster) - stops the compute bill ==" -ForegroundColor Cyan
foreach ($s in $services) {
  aws ecs update-service --cluster $cluster --service "$cluster-$s" --desired-count 0 --region $region | Out-Null
  Write-Host "  $cluster-$s -> 0"
}

if ($Full) {
  Write-Host "== -Full: destroying the ALB (saves ~18/mo; URL will change on up) ==" -ForegroundColor Yellow
  # The ALB is three resources (lb + target groups + listeners) inside the stack module.
  # Each -target must be a pre-quoted string (like $chdir); a bare -target=... gets
  # mangled by PowerShell's native-arg parsing and terraform sees a split target.
  $targets = "-target=module.stack.aws_lb.main", "-target=module.stack.aws_lb_target_group.svc", "-target=module.stack.aws_lb_listener.svc"
  terraform $chdir destroy $targets -auto-approve
  if ($LASTEXITCODE -ne 0) { throw "ALB destroy failed (exit $LASTEXITCODE) - ALB is still up." }
  Write-Host "   ALB gone. On ./up.ps1 you'll get a NEW url -> re-run ./push-images.ps1 -Environment $Environment -Only web." -ForegroundColor Yellow
} else {
  Write-Host "== ALB left running so the URL stays stable. Use -Full to drop it too. ==" -ForegroundColor Green
}

Write-Host "== Down. Tasks paused, data intact. Morning: ./up.ps1 -Environment $Environment ==" -ForegroundColor Green

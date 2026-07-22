# Teardown for the day — pause the resources that cost money, KEEP your data and the
# free foundation (network, security, IAM, cluster, ECR, EFS, secrets all stay).
#
#   Run:  .\down.ps1     (from anywhere)
#   Reverse in the morning:  .\up.ps1
#
# What it pauses:
#   • RDS  -> stopped (compute bill stops; storage ~$2/mo remains; DATA IS KEPT)
#   • ALB  -> destroyed (can't be "stopped"; up.ps1 recreates it in ~2 min)
# Everything else is free/pennies at rest, so we leave it.

$ErrorActionPreference = "Continue"
$id = "agentd-dev-accounts"

Write-Host "== 1/2  Stopping RDS ($id) -- keeps data, stops the compute bill ==" -ForegroundColor Cyan
$status = aws rds describe-db-instances --db-instance-identifier $id --query "DBInstances[0].DBInstanceStatus" --output text 2>$null
if ($status -eq "available") {
    aws rds stop-db-instance --db-instance-identifier $id | Out-Null
    Write-Host "   RDS is stopping (a few minutes)."
} else {
    Write-Host "   RDS status is '$status' -- not stopping (already stopped, or still busy)."
}

Write-Host "== 2/2  Destroying the load balancer (recreated by up.ps1) ==" -ForegroundColor Cyan
terraform -chdir=$PSScriptRoot destroy -target=module.alb -auto-approve

Write-Host "== Done. Costed resources paused; foundation + data remain. Tomorrow: .\up.ps1 ==" -ForegroundColor Green

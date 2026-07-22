# Bring the dev stack back up in the morning — reverse of down.ps1.
#
#   Run:  .\up.ps1     (from anywhere)
#
#   • starts RDS (takes a few minutes to become 'available')
#   • terraform apply -> recreates the ALB and reconciles everything else
#
# NOTE: the ALB's public URL CHANGES each time it's recreated (fine in dev). The script
# prints the new one at the end.

$ErrorActionPreference = "Continue"
$id = "agentd-dev-accounts"

Write-Host "== 1/2  Starting RDS ($id) ==" -ForegroundColor Cyan
$status = aws rds describe-db-instances --db-instance-identifier $id --query "DBInstances[0].DBInstanceStatus" --output text 2>$null
if ($status -eq "stopped") {
    aws rds start-db-instance --db-instance-identifier $id | Out-Null
    Write-Host "   RDS is starting (a few minutes until 'available')."
} else {
    Write-Host "   RDS status is '$status' -- not starting."
}

Write-Host "== 2/2  Recreating the load balancer + reconciling the rest ==" -ForegroundColor Cyan
terraform -chdir=$PSScriptRoot apply -auto-approve

Write-Host "== Up. Your app URL: ==" -ForegroundColor Green
terraform -chdir=$PSScriptRoot output app_url

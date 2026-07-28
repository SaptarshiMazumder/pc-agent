# =============================================================================
# push-images.ps1 - build the 4 Docker images, push them to ECR, and roll the ECS
# services so they pull the fresh images. (Local alternative to the CI Deploy pipeline.)
#
#   PREREQUISITES:
#     - Docker Desktop running
#     - AWS CLI logged in (same creds Terraform uses)
#     - The environment is provisioned (terraform apply) + real keys set (./set-keys.ps1)
#
#   USAGE:
#     ./push-images.ps1                              # dev, all 4 images
#     ./push-images.ps1 -Environment staging         # another environment
#     ./push-images.ps1 -Only web                    # just one image (fast UI re-push)
# =============================================================================
param(
  [string]$Environment = "dev",
  [string]$Only        = ""   # optional: model-proxy | accounts | daemon | web
)

$ErrorActionPreference = "Stop"
$region  = "ap-northeast-1"
$v2      = (Resolve-Path "$PSScriptRoot/../..").Path            # deploy/scripts -> deploy -> v2
$envDir  = Join-Path $v2 "infra/environments/$Environment"
$chdir   = "-chdir=$envDir"                                     # quoted so PowerShell expands it
$cluster = "agentd-$Environment"

if (-not (Test-Path $envDir)) { throw "No such environment: $envDir" }

# --- 1. Read what Terraform built (image repos + the public ALB hostname) ---
Write-Host "Reading Terraform outputs ($Environment)..." -ForegroundColor Cyan
$repos    = terraform $chdir output -json repository_urls | ConvertFrom-Json
$appUrl   = terraform $chdir output -raw app_url               # http://<alb-dns>
$albHost  = ([Uri]$appUrl).Host
$modelProxyRepo = $repos.PSObject.Properties["model-proxy"].Value
$registry = ($modelProxyRepo -split '/')[0]                    # <acct>.dkr.ecr.<region>.amazonaws.com

Write-Host "  registry : $registry"
Write-Host "  ALB host : $albHost"

# --- 2. Log Docker in to ECR ---
# Pass the token as an ARGUMENT, not via `--password-stdin`. Piping into stdin under Windows
# PowerShell re-encodes the string (BOM / wrong encoding) and corrupts the token -> ECR returns
# "400 Bad Request". Passing it as a plain PowerShell arg avoids the text pipeline entirely.
Write-Host "`nLogging Docker in to ECR..." -ForegroundColor Cyan
$pw = aws ecr get-login-password --region $region
if (-not $pw) { throw "Could not get an ECR password. Is the AWS CLI logged in? Try: aws sts get-caller-identity" }
docker login --username AWS --password $pw $registry
if ($LASTEXITCODE -ne 0) { throw "docker login to $registry failed (exit $LASTEXITCODE). Is Docker Desktop running?" }
Write-Host "  login OK" -ForegroundColor Green

# --- 3. Build + push each image ---
# The `web` image bakes the API URLs at BUILD time, so it must know the ALB host.
# accounts listens on :4100, daemon (WebSocket) on :8787 - both via the ALB.
function Build-And-Push($name, $context, $dockerfile, $buildArgs) {
  $uri = "$($repos.PSObject.Properties[$name].Value):latest"
  Write-Host "`n=== $name  ->  $uri ===" -ForegroundColor Green
  $dargs = @("build", "-t", $uri, "-f", $dockerfile)
  foreach ($kv in $buildArgs.GetEnumerator()) { $dargs += @("--build-arg", "$($kv.Key)=$($kv.Value)") }
  $dargs += $context
  docker @dargs
  if ($LASTEXITCODE -ne 0) { throw "build of $name failed (exit $LASTEXITCODE)" }
  docker push $uri
  if ($LASTEXITCODE -ne 0) { throw "push of $name failed (exit $LASTEXITCODE)" }
}

$images = @{
  "model-proxy" = @{ context = "$v2/model-proxy";      dockerfile = "$v2/model-proxy/Dockerfile";          args = @{} }
  accounts = @{ context = $v2;                   dockerfile = "$v2/deploy/docker/Dockerfile.accounts"; args = @{} }
  daemon   = @{ context = $v2;                   dockerfile = "$v2/deploy/docker/Dockerfile";          args = @{} }
  web      = @{ context = "$v2/clients/desktop"; dockerfile = "$v2/clients/desktop/Dockerfile.web";    args = @{
      VITE_AGENTD_ACCOUNTS_URL = "http://$albHost`:4100"
      VITE_AGENTD_URL          = "ws://$albHost`:8787"
  } }
}

$targets = if ($Only) { @($Only) } else { "model-proxy", "accounts", "daemon", "web" }
foreach ($name in $targets) {
  if (-not $images.ContainsKey($name)) {
    throw "Unknown image '$name'. Choose: model-proxy, accounts, daemon, web."
  }
}
foreach ($name in $targets) {
  $i = $images[$name]
  Build-And-Push $name $i.context $i.dockerfile $i.args
}

# --- 4. Roll the services so Fargate pulls the new :latest ---
Write-Host "`nRolling ECS services ($cluster)..." -ForegroundColor Cyan
foreach ($name in $targets) {
  $service = aws ecs describe-services --cluster $cluster --services "$cluster-$name" --region $region --query "services[0].status" --output text
  if ($service -eq "ACTIVE") {
    aws ecs update-service --cluster $cluster --service "$cluster-$name" --force-new-deployment --region $region | Out-Null
    Write-Host "  rolled $cluster-$name"
  } else {
    Write-Host "  skipped $cluster-$name (not created yet; image was pushed)" -ForegroundColor Yellow
  }
}

Write-Host "`nDone. Watch them come up:" -ForegroundColor Cyan
Write-Host "  aws ecs describe-services --cluster $cluster --services $cluster-web $cluster-daemon $cluster-accounts $cluster-model-proxy --region $region --query 'services[].{name:serviceName,running:runningCount,desired:desiredCount}'"
Write-Host "`nThen open:  $appUrl" -ForegroundColor Green

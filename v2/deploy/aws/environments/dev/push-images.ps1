# =============================================================================
# push-images.ps1 - GO LIVE: build the 4 Docker images, push them to ECR, and roll
# the ECS services so they pull the fresh images. Run from anywhere; all paths are
# anchored to this script's location.
#
#   PREREQUISITES (once):
#     - Docker Desktop running
#     - AWS CLI logged in (same creds Terraform uses)
#     - You've set the REAL provider keys in the app secret (./set-keys.ps1)
#
#   USAGE:   ./push-images.ps1              # build + push all 4, then roll the services
#            ./push-images.ps1 -Only web    # just one image (fast re-push after a UI change)
# =============================================================================
param(
  [string]$Only = ""   # optional: gateway | accounts | daemon | web
)

$ErrorActionPreference = "Stop"

$envDir = $PSScriptRoot
$v2     = (Resolve-Path "$envDir/../../../..").Path   # environments/dev -> aws -> deploy -> v2
$region  = "ap-northeast-1"
$cluster = "agentd-dev"

# --- 1. Read what Terraform built (image repos + the public ALB hostname) ---
# NOTE: build the -chdir arg as a quoted string; PowerShell won't expand $envDir
# inside the bare token `-chdir=$envDir` when passing it to a native command.
Write-Host "Reading Terraform outputs..." -ForegroundColor Cyan
$chdir  = "-chdir=$envDir"
$repos  = terraform $chdir output -json repository_urls | ConvertFrom-Json
$appUrl = terraform $chdir output -raw app_url                 # http://<alb-dns>
$albHost = ([Uri]$appUrl).Host
$registry = ($repos.gateway -split '/')[0]                      # <acct>.dkr.ecr.<region>.amazonaws.com

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
  $uri = "$($repos.$name):latest"
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
  gateway  = @{ context = $v2;                   dockerfile = "$v2/deploy/docker/Dockerfile.gateway";  args = @{} }
  accounts = @{ context = $v2;                   dockerfile = "$v2/deploy/docker/Dockerfile.accounts"; args = @{} }
  daemon   = @{ context = $v2;                   dockerfile = "$v2/deploy/docker/Dockerfile";          args = @{} }
  web      = @{ context = "$v2/clients/desktop"; dockerfile = "$v2/clients/desktop/Dockerfile.web";    args = @{
      VITE_AGENTD_ACCOUNTS_URL = "http://$albHost`:4100"
      VITE_AGENTD_URL          = "ws://$albHost`:8787"
  } }
}

$targets = if ($Only) { @($Only) } else { "gateway", "accounts", "daemon", "web" }
foreach ($name in $targets) {
  $i = $images[$name]
  Build-And-Push $name $i.context $i.dockerfile $i.args
}

# --- 4. Roll the services so Fargate pulls the new :latest ---
Write-Host "`nRolling ECS services..." -ForegroundColor Cyan
foreach ($name in $targets) {
  aws ecs update-service --cluster $cluster --service "agentd-dev-$name" --force-new-deployment --region $region | Out-Null
  Write-Host "  rolled agentd-dev-$name"
}

Write-Host "`nDone. Watch them come up:" -ForegroundColor Cyan
Write-Host "  aws ecs describe-services --cluster $cluster --services agentd-dev-web agentd-dev-daemon agentd-dev-accounts agentd-dev-gateway --region $region --query 'services[].{name:serviceName,running:runningCount,desired:desiredCount}'"
Write-Host "`nThen open:  $appUrl" -ForegroundColor Green

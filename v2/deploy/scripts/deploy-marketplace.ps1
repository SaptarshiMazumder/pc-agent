# =============================================================================
# deploy-marketplace.ps1 - build the PUBLIC marketplace page and put it online.
#
# The whole deploy is: build a static bundle, sync it to a bucket, invalidate the CDN.
# There is no image, no ECS service and no rollout, because the marketplace is not a service —
# the page reads catalog.json, which the publish service writes on every publish.
#
#   PREREQUISITES:
#     - AWS CLI logged in (same creds Terraform uses)
#     - The environment is provisioned (terraform apply), so the bucket + distribution exist
#     - npm install has been run once in v2/clients
#
#   USAGE:
#     ./deploy-marketplace.ps1                        # dev
#     ./deploy-marketplace.ps1 -Environment staging
#     ./deploy-marketplace.ps1 -SkipBuild             # re-upload the existing dist/
# =============================================================================
param(
  [string]$Environment = "dev",
  [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$v2      = (Resolve-Path "$PSScriptRoot/../..").Path            # deploy/scripts -> deploy -> v2
$envDir  = Join-Path $v2 "infra/environments/$Environment"
$chdir   = "-chdir=$envDir"
$clients = Join-Path $v2 "clients"
$dist    = Join-Path $clients "marketplace/dist"

if (-not (Test-Path $envDir)) { throw "No such environment: $envDir" }

# --- 1. Read what Terraform built ---
Write-Host "Reading Terraform outputs ($Environment)..." -ForegroundColor Cyan
$bucket  = terraform $chdir output -raw marketplace_site_bucket
$distId  = terraform $chdir output -raw marketplace_distribution_id
$url     = terraform $chdir output -raw marketplace_url
if (-not $bucket) { throw "marketplace_site_bucket is empty - run terraform apply first." }
Write-Host "  bucket : $bucket"
Write-Host "  cdn    : $distId"

# --- 2. Build ---
# Nothing is baked in: the page fetches ./catalog.json from its own origin, which the
# distribution routes to the registry bucket. That is what makes one build serve any deployment.
if (-not $SkipBuild) {
  Write-Host "Building the marketplace page..." -ForegroundColor Cyan
  Push-Location $clients
  try { npm run build:marketplace; if ($LASTEXITCODE -ne 0) { throw "build failed" } }
  finally { Pop-Location }
}
if (-not (Test-Path (Join-Path $dist "index.html"))) { throw "No build at $dist - drop -SkipBuild." }

# --- 3. Sync ---
# --delete so a removed asset actually disappears; the bundle is fingerprinted, so the only
# unfingerprinted file is index.html and it is the one that must never be cached hard.
Write-Host "Uploading to s3://$bucket ..." -ForegroundColor Cyan
aws s3 sync $dist "s3://$bucket" --delete --exclude "index.html" `
  --cache-control "public,max-age=31536000,immutable"
if ($LASTEXITCODE -ne 0) { throw "s3 sync failed" }

aws s3 cp (Join-Path $dist "index.html") "s3://$bucket/index.html" `
  --content-type "text/html" --cache-control "no-cache"
if ($LASTEXITCODE -ne 0) { throw "index.html upload failed" }

# --- 4. Invalidate ---
# Only the entry point: every other file carries a content hash in its name, so a new build
# writes new names and there is nothing stale to purge. Skipping this is the classic "I deployed
# and nothing changed" - visitors keep the previous index.html, which references the old bundle.
Write-Host "Invalidating /index.html ..." -ForegroundColor Cyan
aws cloudfront create-invalidation --distribution-id $distId --paths "/index.html" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "invalidation failed" }

Write-Host ""
Write-Host "Marketplace deployed: $url" -ForegroundColor Green
Write-Host "(catalog.json comes from the registry bucket through the same distribution -" -ForegroundColor DarkGray
Write-Host " publishing an agent updates the store with no deploy here.)" -ForegroundColor DarkGray

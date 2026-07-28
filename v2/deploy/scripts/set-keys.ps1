# =============================================================================
# set-keys.ps1 - seed the app secret in AWS Secrets Manager FROM a per-env file.
#
# The env FILE is the source of truth you keep locally (never git); this pushes its
# provider keys into Secrets Manager via the AWS CLI. It goes CLI-direct, so the real
# key values NEVER touch Terraform state or the repo.
#
# Which keys get pushed is driven by the SECRET'S OWN SCHEMA (defined in Terraform):
# every field the secret already has - except the AWS-generated LITELLM_MASTER_KEY -
# is looked up in the env file and updated if present. Add a new provider key to the
# TF secret + your env file and it flows through automatically (no list to maintain here).
#
#   USAGE:
#     ./set-keys.ps1                          # dev,        reads v2/.env
#     ./set-keys.ps1 -Environment staging     # staging,    reads v2/.env.staging
#     ./set-keys.ps1 -Environment production  # production, reads v2/.env.production
#     ./set-keys.ps1 -EnvFile C:\path\to\some.env   # explicit override
# =============================================================================
param(
  [string]$Environment = "dev",
  [string]$EnvFile     = "",
  [string]$Region      = "ap-northeast-1"
)

$ErrorActionPreference = "Stop"
$v2 = (Resolve-Path "$PSScriptRoot/../..").Path   # deploy/scripts -> deploy -> v2

# Default env file per environment: dev -> v2/.env, others -> v2/.env.<environment>
if (-not $EnvFile) {
  $EnvFile = if ($Environment -eq "dev") { "$v2/.env" } else { "$v2/.env.$Environment" }
}
if (-not (Test-Path $EnvFile)) { throw "Env file not found: $EnvFile" }

$secretId = "agentd/$Environment/app"
$protected = @("LITELLM_MASTER_KEY")   # AWS-generated; never overwrite from a file

# --- Parse the env file into a hashtable (KEY=VALUE; ignores blanks/# comments) ---
$envVars = @{}
foreach ($line in Get-Content $EnvFile) {
  if ($line -match '^\s*#') { continue }
  if ($line -match '^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
    $val = $matches[2].Trim()
    $dq = [char]34
    $sq = [char]39
    if ($val.Length -ge 2 -and (($val[0] -eq $dq -and $val[-1] -eq $dq) -or ($val[0] -eq $sq -and $val[-1] -eq $sq))) {
      $val = $val.Substring(1, $val.Length - 2)   # strip surrounding quotes
    }
    $envVars[$matches[1]] = $val
  }
}

# --- Read the live secret; update each field it defines from the env file ---
$current = aws secretsmanager get-secret-value --secret-id $secretId --region $Region --query SecretString --output text | ConvertFrom-Json

$updated = @()
foreach ($field in $current.PSObject.Properties.Name) {
  if ($protected -contains $field) { continue }
  if ($envVars.ContainsKey($field) -and $envVars[$field]) {
    $current.$field = $envVars[$field]
    $updated += $field
  }
}

if ($updated.Count -eq 0) {
  Write-Host "Nothing to update - no matching keys found in $EnvFile" -ForegroundColor Yellow
  return
}

# Pass the JSON via a temp file (file://...), NOT as a --secret-string arg: Windows PowerShell
# strips the double-quotes out of JSON when handing it to a native command, which would store
# invalid JSON. Write UTF-8 WITHOUT a BOM (a BOM would also break the JSON parse).
$json = $current | ConvertTo-Json -Depth 5
$tmp  = Join-Path $env:TEMP "agentd-secret-$Environment.json"
try {
  [System.IO.File]::WriteAllText($tmp, $json, (New-Object System.Text.UTF8Encoding($false)))
  aws secretsmanager put-secret-value --secret-id $secretId --region $Region --secret-string "file://$tmp" | Out-Null
} finally {
  if (Test-Path $tmp) { Remove-Item $tmp -Force }
}

Write-Host "Pushed to $secretId from $EnvFile" -ForegroundColor Green
$current.PSObject.Properties | ForEach-Object {
  $state = if ($_.Value -eq "REPLACE_ME") { "(still placeholder)" } elseif ($updated -contains $_.Name) { "updated" } else { "unchanged" }
  Write-Host ("  {0,-22} {1}" -f $_.Name, $state)
}

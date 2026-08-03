# =============================================================================
# trace.ps1 - follow ONE chat message from the click to the billed row.
#
# A desktop Cloud-mode request crosses five hops on two machines. Each hop leaves a
# different kind of evidence in a different place, so "is tracing working?" is not one
# question - it is five. This script asks all five and names the hop that broke.
#
#   HOP 1  browser        mints traceId (crypto.randomUUID)          -> shape of the id
#   HOP 2  daemon (PC)    adopts it as run_id, times the run + tools -> ~/.agentd/logs/daemon.log
#   HOP 3  daemon -> AWS  puts X-Agentd-Run-Id on the model request  -> provable only by hop 4
#   HOP 4  model-proxy    recovers it, prices the call               -> CloudWatch
#   HOP 5  accounts       writes the billing row keyed by run_id     -> CloudWatch
#
# HOP 1 IS DETECTABLE FROM THE ID ALONE: the client sends a dashed UUID, and the daemon's
# fallback is uuid4().hex with no dashes. So a dashless id means the client's id never
# arrived - the run is still traced, but not back to the click, and a pre-run rejection
# (already-active session) would have been invisible.
#
# ASCII ONLY, DELIBERATELY: Windows PowerShell 5.1 parses .ps1 as the system ANSI codepage
# unless the file carries a UTF-8 BOM, so a stray em-dash corrupts string quoting and the
# script fails to parse. Keep every character in this file 7-bit.
#
#   USAGE:
#     ./trace.ps1                     # trace the most recent run
#     ./trace.ps1 -RunId 8f3c-...     # a specific one
#     ./trace.ps1 -List               # just show recent run_ids and pick one yourself
# =============================================================================
param(
  [string]$RunId       = "",
  [switch]$List,
  [string]$Environment = "dev",
  [string]$Region      = "ap-northeast-1",
  [int]   $Minutes     = 120,
  [string]$LogPath     = "",
  [int]   $TailLines   = 5000
)

$ErrorActionPreference = "Stop"

function Head($t) { Write-Host "`n$t" -ForegroundColor Cyan }
function Good($t) { Write-Host "  PASS  $t" -ForegroundColor Green }
function Bad ($t) { Write-Host "  FAIL  $t" -ForegroundColor Red }
function Warn($t) { Write-Host "  ??    $t" -ForegroundColor Yellow }

# --- where the daemon's lines are ----------------------------------------------------
# Electron redirects the daemon's stdout to daemon.log, so the EMF lines land there even
# when AGENTD_TELEMETRY_FILE is unset. Prefer the dedicated file when it exists (clean,
# metrics only); fall back to the log (mixed, but always present).
if (-not $LogPath) {
  $candidates = @(
    (Join-Path $PSScriptRoot "..\.telemetry.jsonl"),
    (Join-Path $env:USERPROFILE ".agentd\logs\daemon.log")
  )
  foreach ($c in $candidates) { if (Test-Path $c) { $LogPath = (Resolve-Path $c).Path; break } }
}
if (-not $LogPath -or -not (Test-Path $LogPath)) {
  throw "no daemon telemetry found. Start the app, send a message, then re-run. Looked for .telemetry.jsonl and ~/.agentd/logs/daemon.log"
}

# -Tail, not the whole file: daemon.log is tens of MB and holds months of lines.
$local = Get-Content $LogPath -Tail $TailLines |
  ForEach-Object { try { $_ | ConvertFrom-Json } catch {} } |
  Where-Object { $_ -and $_.run_id }

if (-not $local) {
  throw "no lines carrying a run_id in the last $TailLines lines of $LogPath. Send a chat message first, or raise -TailLines."
}

if ($List) {
  Head "recent runs in $(Split-Path $LogPath -Leaf)"
  $local | Group-Object run_id | ForEach-Object {
    [pscustomobject]@{ run_id = $_.Name; lines = $_.Count; dashed = ($_.Name -like "*-*") }
  } | Select-Object -Last 20 | Format-Table -AutoSize
  return
}

if (-not $RunId) { $RunId = ($local | Select-Object -Last 1).run_id }
Write-Host "tracing $RunId" -ForegroundColor White
Write-Host "  local source: $LogPath" -ForegroundColor DarkGray

$rows = $local | Where-Object { $_.run_id -eq $RunId }

# --- HOP 1: did the id come from the browser? ----------------------------------------
Head "HOP 1 - browser minted the id"
if ($RunId -match '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-') {
  Good "dashed UUID -> the client sent traceId, so the trail starts at the click"
} elseif ($RunId -match '^[0-9a-fA-F]{32}$') {
  Bad  "32 hex chars, no dashes -> this is the daemon's uuid4().hex fallback. The client's traceId never arrived (stale renderer build? check sendMessage in clients/ui/src/state/store.ts)"
} else {
  Warn "unrecognised id shape: $RunId"
}

# --- HOP 2: the daemon's own view of the run -----------------------------------------
Head "HOP 2 - daemon on this PC"
$runRows  = $rows | Where-Object { $_.PSObject.Properties.Name -contains "run_total" }
$durRows  = $rows | Where-Object { $_.PSObject.Properties.Name -contains "run_duration_ms" }
$toolRows = $rows | Where-Object { $_.PSObject.Properties.Name -contains "tool_duration_ms" }
$turns    = ($rows | Where-Object { $_.turn_id } | ForEach-Object { $_.turn_id } | Sort-Object -Unique)

if ($rows) { Good "$($rows.Count) metric line(s) carry this run_id" } else { Bad "no local lines for this run_id" }
if ($durRows) {
  $ms = ($durRows | Select-Object -Last 1).run_duration_ms
  $oc = ($runRows | Select-Object -Last 1).outcome
  Good "run finished: outcome=$oc, $ms ms"
} else {
  Warn "no run_duration_ms. The run may still be in flight, or it was killed mid-run (no SIGTERM handler yet: plan DEF-3)"
}
if ($turns) { Good "$($turns.Count) model turn(s): $($turns -join ', ')" }
if ($toolRows) {
  Write-Host "  tools:" -ForegroundColor DarkGray
  $toolRows | ForEach-Object {
    [pscustomobject]@{ tool = $_.tool; outcome = $_.outcome; ms = $_.tool_duration_ms }
  } | Format-Table -AutoSize
} else {
  Write-Host "  (no tool calls in this run)" -ForegroundColor DarkGray
}

# --- HOPS 3-5: the AWS side ----------------------------------------------------------
$group = "/agentd/$Environment"
$start = [DateTimeOffset]::UtcNow.AddMinutes(-$Minutes).ToUnixTimeSeconds()
$end   = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()

$q = @"
fields @timestamp, service, outcome, model, credential,
       model_cost_usd, credits_charged_total, model_call_total, ledger_row_total,
       ledger_write_total, debit_applied_total, unbilled_cost_usd
| filter run_id = "$RunId"
| sort @timestamp asc
| limit 200
"@

# THE QUERY GOES OVER AS A FILE, NOT AN ARGUMENT. Windows PowerShell strips embedded double
# quotes while building a native command line, so `filter run_id = "abc-123"` reaches the CLI
# unquoted and Insights rejects it with MalformedQueryException pointing partway into the uuid.
# file:// (accepted by the AWS CLI for any parameter) sidesteps shell quoting entirely.
$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("agentd-q-" + [System.Guid]::NewGuid().ToString("N") + ".txt")
# No BOM: the CLI reads the file verbatim, and a BOM would become part of the query text.
[System.IO.File]::WriteAllText($tmp, $q, (New-Object System.Text.UTF8Encoding($false)))
$results = $null
try {
  $uri = "file://" + ($tmp -replace '\\', '/')
  $id = aws logs start-query --log-group-name $group --start-time $start --end-time $end `
          --query-string $uri --region $Region --query queryId --output text
  if (-not $id) { throw "could not start a Logs Insights query on $group" }

  for ($i = 0; $i -lt 60; $i++) {
    $raw = aws logs get-query-results --query-id $id --region $Region --output json | ConvertFrom-Json
    if ($raw.status -eq "Complete") { $results = $raw.results; break }
    if ($raw.status -eq "Failed" -or $raw.status -eq "Cancelled") { throw "query $($raw.status)" }
    Start-Sleep -Milliseconds 700
  }
} finally {
  Remove-Item $tmp -Force -ErrorAction SilentlyContinue
}

$cloud = foreach ($row in $results) {
  $o = [ordered]@{}
  foreach ($f in $row) { if ($f.field -ne "@ptr") { $o[$f.field] = $f.value } }
  [pscustomobject]$o
}

Head "HOP 3 - trace header survived the daemon to AWS jump"
# There is no direct observation point for the header itself: the proxy is the only reader.
# So the proof is transitive - if AWS knows this run_id, the header arrived intact.
if ($cloud) {
  Good "AWS has rows for this run_id, so X-Agentd-Run-Id crossed the wire"
} else {
  Bad  "no AWS rows. Either the model call never happened (refused/cached/local BYOK mode), or the header was dropped. Check model_proxy.apply() and that the app is in Cloud mode."
}

Head "HOP 4 - model-proxy priced the call"
$proxy = $cloud | Where-Object { $_.service -eq "model-proxy" }
if ($proxy) {
  $proxy | Format-Table -AutoSize
  Good "$($proxy.Count) model-proxy row(s)"
} else {
  Bad "nothing from model-proxy"
}

Head "HOP 5 - accounts stored the billing row"
$acc = $cloud | Where-Object { $_.service -eq "accounts" }
if ($acc) {
  $acc | Format-Table -AutoSize
  Good "the money row is keyed by this run_id, so the chain is closed"
} else {
  Bad "nothing from accounts. The cost was measured but never billed. Look for unbilled_cost_usd and ledger_write_total outcome=fail in cloud_check.ps1"
}

# --- verdict -------------------------------------------------------------------------
Head "end to end"
$hops = @(
  [pscustomobject]@{ hop = "1 browser id";      evidence = "id shape";   ok = ($RunId -like "*-*") }
  [pscustomobject]@{ hop = "2 daemon run";      evidence = "your PC";    ok = [bool]$durRows }
  [pscustomobject]@{ hop = "3 header crossed";  evidence = "transitive"; ok = [bool]$cloud }
  [pscustomobject]@{ hop = "4 proxy priced";    evidence = "CloudWatch"; ok = [bool]$proxy }
  [pscustomobject]@{ hop = "5 accounts billed"; evidence = "CloudWatch"; ok = [bool]$acc }
)
$hops | Format-Table -AutoSize
if (($hops | Where-Object { -not $_.ok }).Count -eq 0) {
  Write-Host "ALL FIVE HOPS: one click traced to one billed row, across two machines." -ForegroundColor Green
} else {
  Write-Host "The first broken hop is where to look. Hops above it are proven good." -ForegroundColor Yellow
}

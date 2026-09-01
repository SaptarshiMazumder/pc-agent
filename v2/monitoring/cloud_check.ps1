# =============================================================================
# cloud_check.ps1 - the AWS-side half of the observability check, in one command.
#
# dev_dashboard.py shows what the daemon on YOUR PC is doing. This shows what the
# hosted services (model-proxy, accounts) are doing. Together they cover a desktop
# Cloud-mode request end to end.
#
# WHY THIS EXISTS: the numbers live in CloudWatch Logs as EMF lines, where the metric
# NAME is the field key - so there is no "metric_name" column to filter on, and the
# console's Metrics view deliberately drops run_id/model (they are properties, not
# dimensions, because dimensions are billed per unique combination). Reading this by
# hand in the console means knowing all of that. This script encodes it instead.
#
# ASCII ONLY, DELIBERATELY: Windows PowerShell 5.1 parses .ps1 as the system ANSI
# codepage unless the file carries a UTF-8 BOM, so a stray em-dash corrupts string
# quoting and the whole script fails to parse. Keep every character here 7-bit.
#
#   USAGE:
#     ./cloud_check.ps1                              # last 30 min, dev
#     ./cloud_check.ps1 -Minutes 120                 # wider window
#     ./cloud_check.ps1 -AccountId acct_abc          # also print the credit balance
#     ./cloud_check.ps1 -RunId 8f3c...               # trace ONE request across services
# =============================================================================
param(
  [string]$Environment = "dev",
  [string]$Region      = "ap-northeast-1",
  [int]   $Minutes     = 30,
  [string]$RunId       = "",
  [string]$AccountId   = ""
)

$ErrorActionPreference = "Stop"
$group = "/agentd/$Environment"
$start = [DateTimeOffset]::UtcNow.AddMinutes(-$Minutes).ToUnixTimeSeconds()
$end   = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()

function Head($text) { Write-Host "`n$text" -ForegroundColor Cyan }
function Warn($text) { Write-Host "  $text" -ForegroundColor Yellow }
function Good($text) { Write-Host "  $text" -ForegroundColor Green }
function Bad ($text) { Write-Host "  $text" -ForegroundColor Red }

# --- Logs Insights is asynchronous: start a query, then poll for the result. ---------
# THE QUERY GOES OVER AS A FILE, NOT AN ARGUMENT. Windows PowerShell strips embedded double
# quotes while building a native command line, so `filter run_id = "abc-123"` reaches the CLI
# unquoted and Insights rejects it with MalformedQueryException pointing at a fragment of the
# value. Every query here needs quoted string literals, so file:// (which the AWS CLI accepts
# for any parameter) is the only reliable way to pass them from this shell.
function Invoke-Insights([string]$queryString) {
  $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("agentd-q-" + [System.Guid]::NewGuid().ToString("N") + ".txt")
  # No BOM: the CLI reads the file verbatim, and a BOM would become part of the query text.
  [System.IO.File]::WriteAllText($tmp, $queryString, (New-Object System.Text.UTF8Encoding($false)))
  try {
    $uri = "file://" + ($tmp -replace '\\', '/')
    $id = aws logs start-query --log-group-name $group --start-time $start --end-time $end `
            --query-string $uri --region $Region --query queryId --output text
    if (-not $id) { throw "could not start a query on $group. Does the log group exist?" }
    for ($i = 0; $i -lt 60; $i++) {
      $raw = aws logs get-query-results --query-id $id --region $Region --output json | ConvertFrom-Json
      if ($raw.status -eq "Complete") { return $raw.results }
      if ($raw.status -eq "Failed" -or $raw.status -eq "Cancelled") { throw "query $($raw.status)" }
      Start-Sleep -Milliseconds 700
    }
    throw "query did not finish in 45s"
  } finally {
    Remove-Item $tmp -Force -ErrorAction SilentlyContinue
  }
}

# Insights returns rows as field/value pair lists; reshape so Format-Table can print them.
function ConvertTo-Rows($results) {
  foreach ($row in $results) {
    $o = [ordered]@{}
    foreach ($f in $row) { if ($f.field -ne "@ptr") { $o[$f.field] = $f.value } }
    [pscustomobject]$o
  }
}

Write-Host "agentd cloud check - $group, last $Minutes min" -ForegroundColor White

# --- 1. Is the telemetry library even in the running images? -------------------------
# Every other number in this script is meaningless if this is DISABLED: the metric calls
# become no-ops, so "zero failures" and "no telemetry at all" look identical.
Head "1. telemetry wiring"
$sinceMs = [DateTimeOffset]::UtcNow.AddHours(-6).ToUnixTimeMilliseconds()
foreach ($svc in @("model-proxy", "accounts")) {
  $msg = aws logs filter-log-events --log-group-name $group --log-stream-name-prefix $svc `
           --filter-pattern "telemetry" --start-time $sinceMs --region $Region `
           --query 'events[-1].message' --output text
  if ($msg -match "ENABLED")      { Good "$svc : ENABLED" }
  elseif ($msg -match "DISABLED") { Bad  "$svc : DISABLED - the monitoring library is not in this image; every metric is a silent no-op" }
  else                            { Warn "$svc : no boot line in the last 6h (service may not have restarted since the deploy)" }
}

# --- 2. Money ------------------------------------------------------------------------
# unbilled_cost_usd is the one to stare at: dollars we paid a provider and could NOT
# attribute to an account. It has no other symptom - the user's chat succeeds either way.
Head "2. money (by service)"
$money = ConvertTo-Rows (Invoke-Insights @'
filter ispresent(_aws)
| stats sum(model_call_total)      as calls,
        sum(model_cost_usd)        as cost_usd,
        sum(credits_charged_total) as credits,
        sum(unbilled_cost_usd)     as UNBILLED_usd,
        sum(overspend_usd)         as overspend_usd
  by service
'@)
if ($money) { $money | Format-Table -AutoSize } else { Warn "no metric lines in this window. Has anyone sent a message?" }

Head "3. cost per model"
$byModel = ConvertTo-Rows (Invoke-Insights @'
filter ispresent(model_cost_usd)
| stats sum(model_cost_usd) as cost_usd, sum(credits_charged_total) as credits, count(*) as calls
  by model
| sort cost_usd desc
'@)
if ($byModel) { $byModel | Format-Table -AutoSize } else { Warn "no priced calls yet" }

# --- 4. Anything failing -------------------------------------------------------------
# Includes the two refusal paths (402 no_credits / 403 model_tier), which are SUCCESSES
# of the gate, not faults - read the reason column before worrying.
Head "4. failures and refusals"
$fails = ConvertTo-Rows (Invoke-Insights @'
filter ispresent(_aws) and outcome in ["fail","error","rejected","unavailable","exception","insufficient","overspend"]
| stats count(*) as n by service, outcome, reason
| sort n desc
'@)
if ($fails) { $fails | Format-Table -AutoSize } else { Good "nothing failing" }

$refused = ConvertTo-Rows (Invoke-Insights @'
filter ispresent(run_refused_total)
| stats count(*) as n by service, reason
| sort n desc
'@)
if ($refused) { Write-Host "  gate refusals (expected when testing the cap):"; $refused | Format-Table -AutoSize }

# --- 5. Ledger integrity -------------------------------------------------------------
# buffer_depth > 0 means accounts is unreachable and billing rows are queued in the
# proxy's MEMORY - they are lost if the task is replaced, which every deploy does.
Head "5. ledger integrity"
$ledger = ConvertTo-Rows (Invoke-Insights @'
filter ispresent(ledger_write_total) or ispresent(ledger_buffer_depth)
| stats sum(ledger_write_total) as writes, max(ledger_buffer_depth) as max_buffer_depth
  by outcome, reason
'@)
if ($ledger) { $ledger | Format-Table -AutoSize } else { Warn "no ledger writes recorded" }

# --- 6. Auth ------------------------------------------------------------------------
Head "6. auth (cache=hit means we did not have to call accounts)"
$auth = ConvertTo-Rows (Invoke-Insights @'
filter ispresent(auth_total) or ispresent(login_total)
| stats count(*) as n by service, credential, outcome, cache
| sort n desc
'@)
if ($auth) { $auth | Format-Table -AutoSize } else { Warn "no auth events. Nobody signed in or called the proxy." }

# --- 7. One request, end to end ------------------------------------------------------
if ($RunId) {
  Head "7. trace $RunId"
  $q = @"
fields @timestamp, service, outcome, model, tool,
       model_cost_usd, credits_charged_total, tool_duration_ms, run_duration_ms
| filter run_id = "$RunId"
| sort @timestamp asc
| limit 200
"@
  $trace = ConvertTo-Rows (Invoke-Insights $q)
  if ($trace) {
    $trace | Format-Table -AutoSize
    $svcs = ($trace | ForEach-Object { $_.service } | Sort-Object -Unique) -join ", "
    Good "seen in: $svcs"
  } else {
    Warn "no AWS rows for that run_id. It never reached the proxy, or the trace header was dropped."
  }
}

# --- 8. Credit balance ---------------------------------------------------------------
if ($AccountId) {
  Head "8. credit balance"
  $alb  = aws elbv2 describe-load-balancers --names "agentd-$Environment" --region $Region `
            --query 'LoadBalancers[0].DNSName' --output text
  $sec  = aws secretsmanager get-secret-value --secret-id "agentd/$Environment/app" `
            --region $Region --query SecretString --output text | ConvertFrom-Json
  $view = Invoke-RestMethod "http://${alb}:4100/funding?account_id=$AccountId" `
            -Headers @{ "X-Internal-Key" = $sec.ACCOUNTS_INTERNAL_KEY }
  $view | Format-List
}

Write-Host "`nWhat to want:" -ForegroundColor White
Write-Host "  telemetry ENABLED on both, UNBILLED_usd = 0, no ledger outcome=fail, max_buffer_depth = 0"
Write-Host "  credits_remaining LOWER than before your test messages (that is the billing loop closing)"

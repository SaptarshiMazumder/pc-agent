# =============================================================================
# alarm_check.ps1 - prove the alarms can actually fire.
#
# THE PROBLEM THIS SOLVES. Every money alarm uses treat_missing_data = "notBreaching",
# because no data is the healthy state (nobody expects a steady trickle of failed billing
# writes). That makes `describe-alarms` useless as a check: an alarm whose metric-math
# search matches NOTHING reports exactly the same "OK" as an alarm watching a healthy
# system. A typo in a search expression produces an alarm that looks installed, reports
# healthy forever, and can never fire. That is worse than having no alarm at all.
#
# So this script does not trust the state field. It reads each deployed alarm's OWN metric
# definition back out of AWS, replays it through get-metric-data over a wide window, and
# reports whether it returned any datapoints. Reading the definition from AWS rather than
# re-typing the expressions here is the point: if the two ever disagree, this script is
# testing the wrong thing.
#
# HOW TO READ THE RESULT. "no data" is not automatically a failure - unbilled spend SHOULD
# be empty on a healthy system. It is only a failure for a metric you know exists. Send a
# chat message first, then expect data for the cost, auth and login alarms; those share the
# same search pattern as the rest, so if they resolve, the pattern is correct everywhere.
#
# ASCII ONLY: Windows PowerShell 5.1 parses .ps1 as the system ANSI codepage unless the
# file has a UTF-8 BOM, so a single non-ASCII character breaks string quoting.
#
#   USAGE:
#     ./alarm_check.ps1                 # last 6 hours
#     ./alarm_check.ps1 -Hours 48
# =============================================================================
param(
  [string]$Environment = "dev",
  [string]$Region      = "ap-northeast-1",
  [string]$Project     = "agentd",
  [int]   $Hours       = 6
)

$ErrorActionPreference = "Stop"
$prefix = "$Project-$Environment-"
$start  = [DateTimeOffset]::UtcNow.AddHours(-$Hours).ToString("o")
$end    = [DateTimeOffset]::UtcNow.ToString("o")

Write-Host "alarm check - $prefix* over the last $Hours h" -ForegroundColor White

$raw = aws cloudwatch describe-alarms --alarm-name-prefix $prefix --region $Region --output json | ConvertFrom-Json
if (-not $raw.MetricAlarms -or $raw.MetricAlarms.Count -eq 0) {
  Write-Host "  no alarms with that prefix. Has terraform apply run?" -ForegroundColor Yellow
  return
}

# --- Notification path -------------------------------------------------------------
# An alarm firing into an unconfirmed subscription is silent. Terraform cannot confirm an
# email subscription for you: AWS mails a link a human must click.
Write-Host "`nnotification path" -ForegroundColor Cyan
$topics = $raw.MetricAlarms | ForEach-Object { $_.AlarmActions } | Sort-Object -Unique
foreach ($t in $topics) {
  if (-not $t) { continue }
  $subs = aws sns list-subscriptions-by-topic --topic-arn $t --region $Region --output json | ConvertFrom-Json
  if (-not $subs.Subscriptions -or $subs.Subscriptions.Count -eq 0) {
    Write-Host "  FAIL  $t has NO subscriptions - every alarm fires into the void" -ForegroundColor Red
  } else {
    foreach ($s in $subs.Subscriptions) {
      if ($s.SubscriptionArn -like "*PendingConfirmation*" -or $s.SubscriptionArn -eq "PendingConfirmation") {
        Write-Host "  FAIL  $($s.Endpoint) is PENDING - click the link in the confirmation email" -ForegroundColor Red
      } else {
        Write-Host "  PASS  $($s.Protocol) -> $($s.Endpoint)" -ForegroundColor Green
      }
    }
  }
}

# --- Can each alarm see its metric? -------------------------------------------------
Write-Host "`nalarms" -ForegroundColor Cyan
$rows = @()

foreach ($a in ($raw.MetricAlarms | Sort-Object AlarmName)) {

  # Two alarm shapes: metric-math (Metrics[] holds expressions) and single-metric
  # (Namespace/MetricName/Dimensions on the alarm itself). Rebuild whichever applies.
  if ($a.Metrics -and $a.Metrics.Count -gt 0) {
    $queries = @($a.Metrics)
    $kind    = "math"
  } else {
    $dims = @()
    if ($a.Dimensions) { $dims = @($a.Dimensions | ForEach-Object { @{ Name = $_.Name; Value = $_.Value } }) }
    $queries = @(@{
      Id         = "m1"
      MetricStat = @{
        Metric = @{ Namespace = $a.Namespace; MetricName = $a.MetricName; Dimensions = $dims }
        Period = $a.Period
        Stat   = if ($a.Statistic) { $a.Statistic } else { $a.ExtendedStatistic }
      }
      ReturnData = $true
    })
    $kind = "metric"
  }

  # file://, not an argument: PowerShell strips embedded double quotes when building a
  # native command line, and these queries are entirely double quotes.
  $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("agentd-mdq-" + [System.Guid]::NewGuid().ToString("N") + ".json")
  [System.IO.File]::WriteAllText($tmp, (ConvertTo-Json -InputObject $queries -Depth 12), (New-Object System.Text.UTF8Encoding($false)))

  $points = 0
  $note   = ""
  try {
    $uri = "file://" + ($tmp -replace '\\', '/')
    $out = aws cloudwatch get-metric-data --metric-data-queries $uri `
             --start-time $start --end-time $end --region $Region --output json 2>&1
    if ($LASTEXITCODE -ne 0) {
      $note = "QUERY REJECTED"
    } else {
      $parsed = $out | ConvertFrom-Json
      foreach ($r in $parsed.MetricDataResults) {
        if ($r.Values) { $points += @($r.Values).Count }
        if ($r.StatusCode -and $r.StatusCode -ne "Complete") { $note = $r.StatusCode }
      }
    }
  } catch {
    $note = "ERROR"
  } finally {
    Remove-Item $tmp -Force -ErrorAction SilentlyContinue
  }

  $rows += [pscustomobject]@{
    alarm      = $a.AlarmName -replace [regex]::Escape($prefix), ""
    kind       = $kind
    state      = $a.StateValue
    datapoints = $points
    resolves   = if ($note) { $note } elseif ($points -gt 0) { "yes" } else { "no data" }
  }
}

$rows | Format-Table -AutoSize

$rejected = $rows | Where-Object { $_.resolves -eq "QUERY REJECTED" -or $_.resolves -eq "ERROR" }
$withData = $rows | Where-Object { $_.datapoints -gt 0 }

Write-Host "how to read this" -ForegroundColor White
if ($rejected) {
  Write-Host "  FAIL  $($rejected.Count) alarm(s) have an expression AWS will not evaluate - those can never fire:" -ForegroundColor Red
  $rejected | ForEach-Object { Write-Host "          $($_.alarm)" -ForegroundColor Red }
} else {
  Write-Host "  PASS  every alarm expression is valid and evaluable" -ForegroundColor Green
}
if ($withData) {
  Write-Host "  PASS  $($withData.Count) alarm(s) are seeing live data, which confirms their dimensions match what the services emit" -ForegroundColor Green
} else {
  Write-Host "  ??    no alarm saw data. Send a chat message, wait ~3 min, re-run. If cost/auth/login still" -ForegroundColor Yellow
  Write-Host "        show 'no data', the namespace is wrong: alarms search '$Project' but check what the" -ForegroundColor Yellow
  Write-Host "        tasks emit (AGENTD_TELEMETRY_NAMESPACE)." -ForegroundColor Yellow
}
Write-Host "  'no data' is CORRECT for unbilled-spend, ledger-write-failures, ledger-buffer-backlog and"
Write-Host "  cap-overspend on a healthy system. Those only ever have datapoints when something is wrong."

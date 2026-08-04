# =============================================================================
# scheduler_check.ps1 - prove the billing clock actually runs (plan item 3.8).
#
# WHAT IT IS CHECKING AND WHY describe-* IS NOT ENOUGH. Three accounts endpoints run on a
# schedule: hourly subscription renewals, daily breakage, daily balance-sheet snapshot. Every
# one of them can look perfectly installed and do nothing. `aws scheduler list-schedules`
# proves a cron string exists; it does not prove the Lambda can reach the VPC, that the
# internal key still matches, that DNS resolves accounts.agentd.local, or that the endpoint
# returns 200. And because these run at 00:05 and 00:20 UTC, a broken one is invisible until
# a day after it mattered - by which time subscribers went unbilled.
#
# So this script INVOKES each job with the exact payload the schedule sends, and reports what
# came back. A pass here means the whole chain works: Scheduler role -> Lambda -> ENI -> service
# discovery -> internal key -> endpoint -> ledger. Every endpoint is idempotent, so running
# this against dev (or prod) charges nobody twice and books nothing twice.
#
# ASCII ONLY: Windows PowerShell 5.1 parses .ps1 as the system ANSI codepage unless the file
# has a UTF-8 BOM, so a single non-ASCII character breaks string quoting.
#
#   USAGE:
#     ./scheduler_check.ps1                 # list the schedules, then run all three
#     ./scheduler_check.ps1 -ListOnly       # look, do not touch
#     ./scheduler_check.ps1 -Job ledger-snapshot
# =============================================================================
param(
  [string]$Environment = "dev",
  [string]$Region      = "ap-northeast-1",
  [string]$Project     = "agentd",
  [string]$Job         = "",
  [switch]$ListOnly
)

$ErrorActionPreference = "Stop"
$prefix = "$Project-$Environment-"
$fn     = "${prefix}scheduled-jobs"

function Pass($m) { Write-Host "  PASS  $m" -ForegroundColor Green }
function Fail($m) { Write-Host "  FAIL  $m" -ForegroundColor Red }
function Warn($m) { Write-Host "  ??    $m" -ForegroundColor Yellow }

# Writes $obj as JSON to a temp file and returns a file:// URI. PowerShell strips embedded
# double quotes when building a native command line, so every JSON argument to the AWS CLI
# has to travel as a file. Same trap as trace.ps1 and alarm_check.ps1.
function New-JsonUri($obj, [int]$Depth = 10) {
  $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("agentd-sched-" + [System.Guid]::NewGuid().ToString("N") + ".json")
  $json = if ($obj -is [string]) { $obj } else { ConvertTo-Json -InputObject $obj -Depth $Depth -Compress }
  [System.IO.File]::WriteAllText($tmp, $json, (New-Object System.Text.UTF8Encoding($false)))
  return @{ uri = ("file://" + ($tmp -replace '\\', '/')); path = $tmp }
}

Write-Host "scheduler check - $fn ($Region)" -ForegroundColor White

# --- 1. The clock exists ------------------------------------------------------------
Write-Host "`nschedules" -ForegroundColor Cyan
$listRaw = aws scheduler list-schedules --name-prefix $prefix --region $Region --output json 2>&1
if ($LASTEXITCODE -ne 0) {
  Fail "could not list schedules - this check is inconclusive, not a pass:"
  Write-Host "          $listRaw" -ForegroundColor Red
  return
}
$schedules = ($listRaw | ConvertFrom-Json).Schedules
if (-not $schedules -or @($schedules).Count -eq 0) {
  Fail "no schedules named $prefix* exist. Has terraform apply run since scheduler.tf landed?"
  return
}

$rows = @()
foreach ($s in @($schedules)) {
  # list-schedules omits the cron expression and the target payload, so read each one.
  $d = aws scheduler get-schedule --name $s.Name --region $Region --output json 2>&1 | ConvertFrom-Json
  $payload = @{}
  try { $payload = $d.Target.Input | ConvertFrom-Json } catch { }
  $rows += [pscustomobject]@{
    schedule = $s.Name -replace [regex]::Escape($prefix), ""
    state    = $d.State
    when     = "$($d.ScheduleExpression) $($d.ScheduleExpressionTimezone)"
    calls    = $payload.path
  }
}
$rows | Format-Table -AutoSize

$disabled = $rows | Where-Object { $_.state -ne "ENABLED" }
if ($disabled) {
  Fail "$($disabled.Count) schedule(s) are DISABLED - those jobs are not running at all:"
  $disabled | ForEach-Object { Write-Host "          $($_.schedule)" -ForegroundColor Red }
} else {
  Pass "all $($rows.Count) schedules are ENABLED"
}

# The snapshot's cadence is the one that costs money: CloudWatch bills custom metrics per
# metric per month prorated hourly, so 5-minute sampling keeps 11 gauges billable for the
# whole month (~$3.30) while daily touches ~30 hours (~$0.14).
$snap = $rows | Where-Object { $_.calls -eq "/ledger/snapshot" }
if ($snap -and ($snap.when -match "rate\(\d+ (minute|hour)" -or $snap.when -match "\*/")) {
  Warn "ledger-snapshot runs at '$($snap.when)'. Sub-daily sampling keeps its 11 gauges billable"
  Write-Host "        for every hour of the month (~`$3.30/mo vs ~`$0.14 daily). Intended?" -ForegroundColor Yellow
}

if ($ListOnly) { return }

# --- 2. The function config ---------------------------------------------------------
Write-Host "`nfunction" -ForegroundColor Cyan
$cfgRaw = aws lambda get-function-configuration --function-name $fn --region $Region --output json 2>&1
if ($LASTEXITCODE -ne 0) {
  Fail "$fn does not exist or cannot be read:"
  Write-Host "          $cfgRaw" -ForegroundColor Red
  return
}
$cfg = $cfgRaw | ConvertFrom-Json
Pass "$($cfg.FunctionName) runtime=$($cfg.Runtime) timeout=$($cfg.Timeout)s state=$($cfg.State)"
if ($cfg.VpcConfig -and $cfg.VpcConfig.VpcId) {
  # In the VPC is the point: it lets the function reach accounts by its private name, so the
  # internal key never crosses the public internet.
  Pass "in VPC $($cfg.VpcConfig.VpcId) - the internal key stays inside the network"
} else {
  Warn "NOT in a VPC. It must be reaching accounts over the public ALB, which puts the"
  Write-Host "        internal key (full ledger access) on the wire in cleartext." -ForegroundColor Yellow
}

# --- 3. Run the jobs ----------------------------------------------------------------
# The real test. Same payload the scheduler sends; safe to repeat because every endpoint is
# idempotent (renewals key on subscription+period, breakage per grant, snapshot is a read).
Write-Host "`ninvoking each job (same payload the clock sends)" -ForegroundColor Cyan
$targets = $rows
if ($Job) { $targets = $rows | Where-Object { $_.schedule -eq $Job } }
if (-not $targets) { Fail "no schedule named '$Job'"; return }

$anyFail = $false
foreach ($t in @($targets)) {
  $p   = New-JsonUri @{ job = "manual:$($t.schedule)"; path = $t.calls }
  $out = Join-Path ([System.IO.Path]::GetTempPath()) ("agentd-sched-out-" + [System.Guid]::NewGuid().ToString("N") + ".json")
  try {
    # raw-in-base64-out: CLI v2 treats --payload as base64 unless told the input is raw.
    $inv = aws lambda invoke --function-name $fn --payload $p.uri `
             --cli-binary-format raw-in-base64-out --log-type Tail `
             --region $Region --output json $out 2>&1
    if ($LASTEXITCODE -ne 0) {
      $anyFail = $true
      Fail "$($t.schedule) - could not invoke:"
      Write-Host "          $inv" -ForegroundColor Red
      continue
    }
    $meta = $inv | ConvertFrom-Json
    $body = ""
    if (Test-Path $out) { $body = (Get-Content $out -Raw) }

    if ($meta.FunctionError) {
      # The handler re-raises on any failure precisely so this shows up here AND in the
      # AWS/Lambda Errors metric the alarm watches.
      $anyFail = $true
      Fail "$($t.schedule) ($($t.calls)) FAILED - $($meta.FunctionError)"
      Write-Host "          $body" -ForegroundColor Red
      if ($meta.LogResult) {
        $log = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($meta.LogResult))
        Write-Host "          --- last log lines ---" -ForegroundColor DarkGray
        $log -split "`n" | Select-Object -Last 12 | ForEach-Object { Write-Host "          $_" -ForegroundColor DarkGray }
      }
    } else {
      Pass "$($t.schedule) -> $($t.calls)"
      # The result body IS the audit trail: renewed / grants_closed / balanced live here.
      Write-Host "          $body" -ForegroundColor DarkGray
    }
  } finally {
    Remove-Item $p.path -Force -ErrorAction SilentlyContinue
    Remove-Item $out -Force -ErrorAction SilentlyContinue
  }
}

# --- 4. Did the snapshot's gauges reach CloudWatch? ---------------------------------
# The snapshot's whole purpose is to turn ledger levels into metrics, so "the endpoint
# returned 200" is only half a pass. Extraction is asynchronous, so allow a minute.
if (-not $Job -or $Job -eq "ledger-snapshot") {
  Write-Host "`nbalance sheet in CloudWatch" -ForegroundColor Cyan
  $q = New-JsonUri @(
    @{ Id = "m1"; ReturnData = $true; MetricStat = @{
        Metric = @{ Namespace = $Project; MetricName = "ledger_balanced"
                    Dimensions = @(@{ Name = "service"; Value = "accounts" }) }
        Period = 300; Stat = "Maximum" } },
    @{ Id = "m2"; ReturnData = $true; MetricStat = @{
        Metric = @{ Namespace = $Project; MetricName = "credit_liability_usd"
                    Dimensions = @(@{ Name = "service"; Value = "accounts" }) }
        Period = 300; Stat = "Maximum" } }
  ) 12
  try {
    $mdRaw = aws cloudwatch get-metric-data --metric-data-queries $q.uri `
               --start-time ([DateTimeOffset]::UtcNow.AddHours(-2).ToString("o")) `
               --end-time ([DateTimeOffset]::UtcNow.ToString("o")) `
               --region $Region --output json 2>&1
    if ($LASTEXITCODE -ne 0) {
      Warn "could not read metrics back: $mdRaw"
    } else {
      $md = $mdRaw | ConvertFrom-Json
      foreach ($r in $md.MetricDataResults) {
        $vals = @($r.Values)
        if ($vals.Count -eq 0) {
          Warn "$($r.Label): no datapoints yet. EMF extraction lags the log line by up to a"
          Write-Host "        minute; re-run. If it stays empty the namespace is wrong (alarms and" -ForegroundColor Yellow
          Write-Host "        dashboards read '$Project'; check AGENTD_TELEMETRY_NAMESPACE on the task)." -ForegroundColor Yellow
        } elseif ($r.Label -eq "ledger_balanced" -and $vals[0] -ne 1) {
          Fail "ledger_balanced = $($vals[0]). A posting bypassed ledger.post() and every"
          Write-Host "          money number in the system is suspect until that is found." -ForegroundColor Red
          $anyFail = $true
        } else {
          Pass "$($r.Label) = $($vals[0]) (latest of $($vals.Count) datapoints)"
        }
      }
    }
  } finally {
    Remove-Item $q.path -Force -ErrorAction SilentlyContinue
  }
}

# --- 5. Is anything watching? -------------------------------------------------------
Write-Host "`nalarm" -ForegroundColor Cyan
$alRaw = aws cloudwatch describe-alarms --alarm-names "${prefix}scheduled-jobs-failing" `
           --region $Region --output json 2>&1
if ($LASTEXITCODE -eq 0) {
  $al = ($alRaw | ConvertFrom-Json).MetricAlarms
  if (@($al).Count -gt 0) {
    Pass "$($al[0].AlarmName) is installed, state=$($al[0].StateValue)"
  } else {
    Warn "no scheduled-jobs-failing alarm found - a job could start failing silently"
  }
} else {
  Warn "could not read alarms: $alRaw"
}

Write-Host "`nhow to read this" -ForegroundColor White
if ($anyFail) {
  Write-Host "  Something in the clock is broken. The three usual causes, in order:" -ForegroundColor Red
  Write-Host "    401 from the endpoint  -> the internal key in the Lambda env no longer matches the"
  Write-Host "                              one in Secrets Manager (re-apply, or set-keys.ps1 rotated it)"
  Write-Host "    timeout / unreachable  -> the accounts task is down, or the service security group"
  Write-Host "                              lost the ingress rule from the Lambda's group"
  Write-Host "    500 from the endpoint  -> read the result body above; it is the app's own error"
} else {
  Write-Host "  Every job runs end to end. The schedules will do exactly what you just watched them do."
  Write-Host "  Next: turn on the stopped-clock alarm, which catches the failure this script cannot"
  Write-Host "  (nothing fails because nothing runs) - set enable_job_absence_alarm = true and apply."
}

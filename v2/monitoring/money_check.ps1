# =============================================================================
# money_check.ps1 - exercise the whole money path against the LIVE service.
#
# cloud_check.ps1 reads what already happened. This one MAKES things happen: it creates a
# throwaway account, sells it a product, replays the purchase, writes a usage row twice, and
# checks the books balance at the end. Every assertion is about a rule the business depends on,
# not about an implementation detail.
#
# WHAT IT PROVES
#   1  the deployed image is the new one           (/ledger/balances exists at all)
#   2  a purchase splits into fee / reserve / margin and the parts sum to the whole
#   3  a replayed purchase charges ONCE            (the one bug you cannot recover from)
#   4  a creator accrues, and only via a product that names one
#   5  buying an agent product entitles the buyer
#   6  a replayed usage write bills ONCE           (DEF-11)
#   7  credits actually drain
#   8  the double-entry books balance
#
# SAFE BUT NOT READ-ONLY. It creates a real account, a real product and real ledger rows in
# whichever environment you point it at. Fine for dev; do not aim it at production.
#
# ASCII ONLY: Windows PowerShell 5.1 parses .ps1 as the system ANSI codepage unless the file
# carries a UTF-8 BOM, so one non-ASCII character breaks string quoting.
#
#   USAGE:  ./money_check.ps1
#           ./money_check.ps1 -Environment dev -Region ap-northeast-1
# =============================================================================
param(
  [string]$Environment = "dev",
  [string]$Region      = "ap-northeast-1",
  [string]$Project     = "agentd",
  [double]$Price       = 20.0
)

$ErrorActionPreference = "Stop"
$script:failures = 0

function Head($t) { Write-Host "`n$t" -ForegroundColor Cyan }
function Pass($t) { Write-Host "  PASS  $t" -ForegroundColor Green }
function Fail($t) { Write-Host "  FAIL  $t" -ForegroundColor Red; $script:failures++ }
function Note($t) { Write-Host "        $t" -ForegroundColor DarkGray }
function Check($ok, $t) { if ($ok) { Pass $t } else { Fail $t } }

# --- 0. where is it, and what is the key ------------------------------------------
$alb = aws elbv2 describe-load-balancers --names "$Project-$Environment" --region $Region `
         --query 'LoadBalancers[0].DNSName' --output text
if (-not $alb -or $alb -eq "None") { throw "no load balancer named $Project-$Environment" }
$acct = "http://${alb}:4100"
$sec = aws secretsmanager get-secret-value --secret-id "$Project/$Environment/app" `
         --region $Region --query SecretString --output text | ConvertFrom-Json
$hdr = @{ "X-Internal-Key" = $sec.ACCOUNTS_INTERNAL_KEY }

Write-Host "money check - $acct" -ForegroundColor White

# --- 1. is the NEW image actually running? ---------------------------------------
# Everything below is meaningless against the old image, and the failure would look like a
# bewildering 404 rather than "you did not deploy".
Head "1. deployed code"
try {
  $ready = Invoke-RestMethod "$acct/health/ready"
  Check ($ready.db -eq "writable") "/health/ready says the database is writable"
} catch {
  Fail "/health/ready is missing or failing - the accounts image is OLD or the DB is read-only"
  Note $_.Exception.Message
}
try {
  $null = Invoke-RestMethod "$acct/ledger/balances" -Headers $hdr
  Pass "/ledger/balances exists - the ledger shipped"
} catch {
  Fail "/ledger/balances is missing - this is the pre-Phase-2 image. Stop here and redeploy."
  Note $_.Exception.Message
  exit 1
}

# --- 2. a throwaway buyer --------------------------------------------------------
Head "2. test account"
$email = "moneycheck-" + [guid]::NewGuid().ToString("N").Substring(0, 8) + "@test.local"
$me = Invoke-RestMethod -Method Post "$acct/signup" -ContentType application/json `
        -Body (@{ email = $email; password = "testpass1234" } | ConvertTo-Json)
$aid = $me.account_id
Pass "created $email"
Note "account_id $aid"

# --- 3. something to sell, WITH a creator ---------------------------------------
# A bare {usd: 20} purchase has no creator, so creator_payable stays 0 and the split looks
# broken. The creator's share only exists when a product names one.
Head "3. product with a creator"
$pid = "moneycheck-agent"
$prod = Invoke-RestMethod -Method Post "$acct/products" -Headers $hdr -ContentType application/json `
  -Body (@{ id = $pid; kind = "agent_subscription"; title = "money check"; creator_id = "bob-test";
            agent_id = "moneycheck-agent"; price_usd = $Price; scope = "agent:moneycheck-agent";
            period_days = 30 } | ConvertTo-Json)
Check ($prod.credits -gt 0) "product priced at `$$Price -> $($prod.credits) credits"

# --- 4. buy it ------------------------------------------------------------------
Head "4. purchase"
$idem = "mc-" + [guid]::NewGuid().ToString("N").Substring(0, 10)
$buy = Invoke-RestMethod -Method Post "$acct/purchase" -Headers $hdr -ContentType application/json `
  -Body (@{ account_id = $aid; product_id = $pid; idempotency_key = $idem } | ConvertTo-Json)
$s = $buy.split
Note ("gross {0:N2}  fee {1:N2}  reserve {2:N2}  margin {3:N2}  creator {4:N2}  platform {5:N2}" -f `
      $s.gross_micros, $s.fee_micros, $s.reserve_micros, $s.margin_micros, $s.creator_micros, $s.platform_micros)

Check ($buy.replayed -eq $false) "first purchase is not a replay"
# Internal consistency rather than hardcoded figures: the dials are server-side, so asserting
# $6.00 would fail the moment someone retunes the markup. These must hold at ANY setting.
$sum = [math]::Round($s.fee_micros + $s.reserve_micros + $s.margin_micros, 2)
Check ($sum -eq [math]::Round($s.gross_micros, 2)) "fee + reserve + margin = gross ($sum)"
$parts = [math]::Round($s.creator_micros + $s.platform_micros, 2)
Check ($parts -eq [math]::Round($s.margin_micros, 2)) "creator + platform = margin ($parts)"
Check ($s.margin_micros -gt 0) "the sale is profitable (margin > 0)"
Check ($buy.funding_source -eq "agent_subscription") "credits are siloed to the agent, not the platform pool"
$creditsAfterBuy = $buy.credits_remaining
Note "credits_remaining $creditsAfterBuy"

# --- 5. the same purchase again -------------------------------------------------
# The one bug a payments system cannot come back from. A user retrying a slow checkout must not
# be charged twice.
Head "5. replayed purchase"
$again = Invoke-RestMethod -Method Post "$acct/purchase" -Headers $hdr -ContentType application/json `
  -Body (@{ account_id = $aid; product_id = $pid; idempotency_key = $idem } | ConvertTo-Json)
Check ($again.replayed -eq $true) "reported as a replay"
Check ($again.txn_id -eq $buy.txn_id) "returns the ORIGINAL transaction, not a new one"
Check ($again.credits_remaining -eq $creditsAfterBuy) "no second grant of credits"

# --- 6. entitlement -------------------------------------------------------------
Head "6. entitlement"
$ent = Invoke-RestMethod "$acct/entitlement?account_id=$aid&agent_id=moneycheck-agent" -Headers $hdr
Check ($ent.entitled -eq $true) "buyer may run the agent (source=$($ent.source))"
$fund = Invoke-RestMethod "$acct/funding?account_id=$aid&agent_id=moneycheck-agent" -Headers $hdr
Check ($fund.entitlement_required -eq $true) "an agent that is FOR SALE is gated"
Check ($fund.entitled -eq $true) "and this buyer passes the gate"
$other = Invoke-RestMethod "$acct/funding?account_id=$aid&agent_id=main" -Headers $hdr
Check ($other.entitlement_required -eq $false) "an agent nobody sells is NOT gated"

# --- 7. usage, written twice ----------------------------------------------------
Head "7. usage write (DEF-11: exactly once)"
$evt = "mc-evt-" + [guid]::NewGuid().ToString("N").Substring(0, 10)
$row = @{ account_id = $aid; model = "moneycheck/model"; in_tokens = 1000; out_tokens = 200;
          cached_tokens = 800; cost_usd = 0.01; credits = 1667; run_id = "mc-run";
          turn_id = "mc-run-1"; agent_id = "moneycheck-agent"; model_tier = "cheap";
          funding_source = "agent_subscription"; event_id = $evt }
$u1 = Invoke-RestMethod -Method Post "$acct/usage" -Headers $hdr -ContentType application/json `
        -Body ($row | ConvertTo-Json)
$u2 = Invoke-RestMethod -Method Post "$acct/usage" -Headers $hdr -ContentType application/json `
        -Body ($row | ConvertTo-Json)
Check ($u1.duplicate -eq $false) "first write lands"
Check ($u2.duplicate -eq $true) "second write is recognised as a replay, not an error"
Check ($u1.spent_usd -eq $u2.spent_usd) "spend counted ONCE ($($u2.spent_usd))"

# --- 8. debit actually drains ---------------------------------------------------
Head "8. credits drain"
$deb = Invoke-RestMethod -Method Post "$acct/debit" -Headers $hdr -ContentType application/json `
  -Body (@{ account_id = $aid; agent_id = "moneycheck-agent"; credits = 5000; run_id = "mc-run" } | ConvertTo-Json)
Check ($deb.credits_remaining -lt $creditsAfterBuy) `
  "balance fell $creditsAfterBuy -> $($deb.credits_remaining)"

# --- 9. the books ---------------------------------------------------------------
Head "9. double-entry books"
$bal = Invoke-RestMethod "$acct/ledger/balances" -Headers $hdr
Check ($bal.balanced -eq $true) "balanced (residual $($bal.residual_usd))"
Check ($bal.accounts.creator_payable -gt 0) "a creator is owed money ($($bal.accounts.creator_payable))"
Check ($bal.accounts.inference_reserve -gt 0) "inference is reserved ($($bal.accounts.inference_reserve))"
Check ($bal.accounts.user_credit_liability -gt 0) "prepaid credits are carried as a LIABILITY, not revenue"
Note ("gross_margin_usd {0}" -f $bal.gross_margin_usd)

# --- 10. the scheduled jobs run without exploding -------------------------------
Head "10. scheduled endpoints"
$snap = Invoke-RestMethod -Method Post "$acct/ledger/snapshot" -Headers $hdr
Check ($snap.balanced -eq $true) "snapshot published the balance sheet as metrics"
Note "credits_outstanding $($snap.credits_outstanding)"
$ren = Invoke-RestMethod -Method Post "$acct/subscriptions/renew-due" -Headers $hdr
Check ($ren.renewed -eq 0) "nothing renews before its period ends (renewed=$($ren.renewed))"
$exp = Invoke-RestMethod -Method Post "$acct/ledger/close-expired" -Headers $hdr
Pass "close-expired ran (grants_closed=$($exp.grants_closed))"

# --- verdict --------------------------------------------------------------------
Head "verdict"
if ($script:failures -eq 0) {
  Write-Host "  ALL CHECKS PASSED - the money path works end to end on the deployed build." -ForegroundColor Green
} else {
  Write-Host "  $($script:failures) CHECK(S) FAILED - see FAIL lines above." -ForegroundColor Red
}
Write-Host ""
Write-Host "left behind on purpose, so you can inspect it:"
Write-Host "  account   $email  ($aid)"
Write-Host "  product   $pid"
Write-Host "  entries   Invoke-RestMethod `"$acct/ledger/entries?account_id=$aid`" -Headers `$hdr"
Write-Host ""
Write-Host "metrics take 1-3 min to appear. Then:"
Write-Host "  ./cloud_check.ps1 -AccountId $aid"
Write-Host "  the '$Project-$Environment-business' dashboard in CloudWatch"
if ($script:failures -gt 0) { exit 1 }

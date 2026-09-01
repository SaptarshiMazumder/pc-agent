# `cap-overspend`

> `debit_applied_total{service=accounts, outcome=overspend}` Sum > 0 over 5 minutes.

## What broke

An account spent past its credit balance. It should be impossible: `/debit` is a hard stop that
never goes negative and never partially debits, and the proxy checks the balance before every
uncached model call.

## What it costs while it lasts

Whatever the overspend is, directly — but the more important cost is that **a load-bearing
assumption has failed**. The reserve is exact rather than estimated *because* the cap is hard
(design decision D2). That is what makes it safe to pay a creator at the moment of sale while the
inference is still to come. If the cap can be exceeded, the reserve is no longer sufficient and
every margin number is optimistic.

## What to check

Who, and by how much:

```
filter ispresent(debit_applied_total) and outcome = "overspend"
| stats count(*) as n, sum(credits) as credits by account_id
| sort credits desc
```

Then their actual books:

```powershell
$acct = "http://<alb>:4100"
Invoke-RestMethod "$acct/funding?account_id=<id>" -Headers @{ "X-Internal-Key" = $key }
Invoke-RestMethod "$acct/ledger/entries?account_id=<id>&limit=50" -Headers @{ "X-Internal-Key" = $key }
```

## How to fix it

1. **Concurrent calls raced the check.** The likeliest cause by far. The proxy checks the balance,
   then the call runs, then the debit lands — two calls that both pass the check can both spend.
   Look for several `run_id`s within a second or two on the same account. This is a known window,
   not a bug in the debit itself; the fix is a reservation at check time, not a bigger cap.
2. **A grant expired mid-run.** The check saw a live grant; by the time the debit landed it had
   expired, so `_live_grants` no longer counted it. Look for an `expires_at` in the run's window.
3. **The proxy skipped the pre-check.** If `run_refused_total{reason=no_credits}` is zero over a
   period where balances hit zero, the gate is not running at all — check the accounts URL and
   internal key on the proxy task.

## What NOT to do

Do not zero the account's balance to "clean it up". The ledger is double-entry: an unexplained
adjustment leaves `balanced` true while making the history wrong, which is exactly the failure
double-entry exists to prevent. Post a correcting transaction, or leave it and note it.

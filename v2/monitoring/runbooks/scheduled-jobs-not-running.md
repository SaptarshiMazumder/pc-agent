# `scheduled-jobs-not-running`

> `AWS/Lambda Invocations{FunctionName=agentd-<env>-scheduled-jobs}` Sum < 1 over 24 hours.
> **`treat_missing_data = "breaching"`.** **Ships DISABLED** (`enable_job_absence_alarm = false`).

## What broke

The billing clock has stopped. Not "a job failed" — [`scheduled-jobs-failing`](scheduled-jobs-failing.md)
covers that, and it can only fire when something actually executes. This is the other half:
**nothing is executing at all**, so there is nothing to fail, and every error-based alarm reports
perfect health.

## What it costs while it lasts

Subscriptions are not being renewed. No customer notices — their credits keep working until the
period they already paid for runs out — and no alarm fires, because a schedule that does not run
generates neither errors nor traffic. Revenue simply stops arriving, and the first symptom is a
number being lower than expected on a dashboard nobody checks daily.

This is the most quietly expensive failure in the entire system, which is why it has an alarm that
treats silence as breaking.

## Why it ships disabled

`treat_missing_data = "breaching"` means the alarm fires during any window with no data — including
the window between `terraform apply` creating it and the first schedule firing up to 24 hours
later. Enabled by default it would send a spurious page on every fresh environment, and an alarm
whose first act is to cry wolf gets muted.

**Turn it on once the schedules have run at least once:**

```powershell
cd v2\monitoring
.\scheduler_check.ps1        # confirms all three run end to end
```

then set `enable_job_absence_alarm = true` and apply.

## What to check

Do the schedules still exist, and are they enabled?

```powershell
aws scheduler list-schedules --name-prefix agentd-dev --region ap-northeast-1
.\scheduler_check.ps1 -ListOnly
```

Has the function been invoked at all?

```
fields @timestamp, job, outcome
| sort @timestamp desc
| limit 20
```
(log group `/aws/lambda/agentd-dev-scheduled-jobs`)

## How to fix it

1. **A schedule was deleted or disabled.** `list-schedules` returns fewer than three, or one shows
   `State: DISABLED`. Check `var.scheduled_job_overrides` — an environment override with
   `enabled = false` is the intended way to turn a job off, and also the easiest way to forget one
   is off.
2. **The Scheduler role lost permission to invoke the Lambda.** Scheduler retries and gives up
   silently; there is no error on our side because our code never ran. `terraform plan` will show
   the missing `aws_iam_role_policy.scheduler_invoke`.
3. **The function was deleted.** A `terraform destroy` on the wrong workspace, or a manual
   cleanup. `terraform apply` restores it.
4. **The alarm is watching the wrong function name.** If the environment was renamed, the
   dimension no longer matches anything — and with `breaching`, an alarm pointed at nothing fires
   forever. Check `FunctionName` on the alarm against the deployed function.

## After fixing

Run the jobs by hand once before you consider it resolved — renewals missed during the outage are
still due, and `renew-due` charges each subscription for the period it renews *into*, so nothing
was lost by the delay:

```powershell
.\scheduler_check.ps1
```

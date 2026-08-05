# Runbooks — one per alarm (plan item 5.5)

An alarm email tells you something is wrong. A runbook is the difference between that being
useful at 3am and being an invitation to guess.

**Every file here is named after the alarm's suffix.** `agentd-dev-unbilled-spend` →
[`unbilled-spend.md`](unbilled-spend.md). That is the whole index: the alarm name IS the filename,
so nobody has to find the right page.

## The shape

Each runbook answers four questions, in the order you need them under pressure:

1. **What broke** — what the alarm actually measures, in terms of the system rather than the metric.
2. **What it costs while it lasts** — the thing that decides whether you fix it now or in the morning.
3. **What to check** — a Logs Insights query or a command, ready to paste.
4. **How to fix it** — the usual causes, most likely first.

## Read this before the first one

**Every alarm here uses `treat_missing_data = "notBreaching"`** except the two absence alarms
(`no-successful-logins`, `scheduled-jobs-not-running`). For a money alarm, no data IS health —
nobody expects a steady trickle of failed billing writes. The consequence is that an alarm whose
metric never resolves reports OK forever and can never fire. `monitoring/alarm_check.ps1` is what
distinguishes "healthy" from "broken and silent"; run it after any change to `emf.py`'s dimension
rollups or to `alarms.tf`.

**The log group is `/agentd/<env>`** — `/agentd/dev` for everything below. Scheduled jobs are the
exception: they run in Lambda, so their lines are in `/aws/lambda/agentd-<env>-scheduled-jobs`.

**Queries assume Logs Insights.** Console → CloudWatch → Logs Insights → pick the log group →
paste. `monitoring/QUERIES.md` has the general-purpose ones; these are the incident-specific ones.

**The metric name is the field key.** There is no `metric_name` field to filter on — that is why
every query starts `filter ispresent(<the_metric>)`.

## When the answer is "I do not know"

Take the run id from the alarm's diagnosing query and run `monitoring/trace.ps1 -RunId <id>`. It
follows one message across all five hops (browser → daemon → proxy → accounts → ledger) and tells
you which one it stopped at. That is almost always faster than reasoning about which service is
at fault.

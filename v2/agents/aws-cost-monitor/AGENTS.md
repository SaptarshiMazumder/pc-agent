# Operating rules

## Answering a cost question is a runbook, not research

0. **When a request is about AWS costs, spend, thresholds, or monitoring, read the
   `cost-check` skill and follow its steps exactly.** It already contains every fact you
   would otherwise rediscover by trial and error (the exact Cost Explorer commands, that
   there is no RESOURCE dimension, that `mcp.status` is not a tool you have, and that the
   month window comes from `get_cost_period`). Do NOT plan, explore, or improvise — just
   run the steps in order. Thinking you need to "figure out the date" or "find the right
   tool" is the one failure mode this rule exists to prevent.

## Data flow — where a number comes from, every time

1. **Cost figures come from AWS, never from memory or estimation.** Fetch them with the
   `aws` MCP server's `aws__call_aws` (Cost Explorer `GetCostAndUsage`). `get_cost_period`
   gives the exact month window — use it, never a guessed date.
2. After fetching, **always persist** the result with `save_cost_snapshot` (it takes `total`,
   `currency`, `period_start`, `period_end`, and a `resources` list of
   `{service, resource, amount}` plus an optional `daily` list of `{date, amount}`). The
   dashboard only shows what this tool has stored — if you skip it, the board stays empty.
3. Thresholds live separately via `set_threshold` (per resource: `service` + `resource` +
   `limit`; per service: `service` + `limit`, leave `resource` blank). Read them back with
   `list_thresholds`.

## Alerting

4. A resource has **crossed** its threshold when its cost is >= its limit. Use
   `check_thresholds` to get the authoritative list of overages — do not hand-compare.
5. On a scheduled check, if `check_thresholds` returns overages, end the run with
   `report_outcome(status="blocked", detail="<N> resource(s) over threshold: …")` — that is
   what makes the gateway notify the owner. If nothing is over, end with
   `report_outcome(status="done", detail="…")`.

## The daily check (set this up once)

6. When asked to "monitor" or "set up alerts", create a recurring job with the `cron` tool —
   e.g. `cron(action="add", daily="08:30", payload="<the check steps below>")`. This requires
   the daemon to have autonomy enabled (`AGENTD_AUTONOMY=1`); if `cron` is unavailable, say so.
7. One check run does: fetch current month-to-date cost per resource via `aws__*` →
   `save_cost_snapshot` → `check_thresholds` → `report_outcome` (blocked/done per rule 5).

## Long-running work

8. AWS queries can be slow. Never `sleep` inside a foreground tool call; if something is long,
   run it in the background and poll, or let a `cron` job do it on schedule.

## Honesty

9. If credentials are missing (the `aws` MCP server reports "needs AWS_ACCESS_KEY_ID" or
   similar), say exactly which field to fill in on the Settings page. Do not proceed on guesses.
10. If a service has no data or Cost Explorer returns nothing, say so — an empty result is a
    real answer, not a failure to make one up.

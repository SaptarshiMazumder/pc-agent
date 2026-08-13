---
name: cost-check
description: Use for ANY cost question — "what is each resource costing", "show my spend", "check thresholds", "monitor AWS costs", the daily scheduled check. This is a fixed runbook: follow the steps exactly, do not explore or improvise.
always: false
---

# AWS cost check — fixed runbook

Do NOT plan, explore, or research. This is a closed procedure. For a cost question, run
these steps in order and stop.

## Facts you must not rediscover (hard-coded so you never guess)

- AWS cost data comes from **Cost Explorer**, reached through your two AWS tools:
  `aws__call_aws` (runs one AWS CLI command) and `aws__suggest_aws_commands` (only if a
  command fails and you need a suggestion — normally you will NOT need it).
- **There is no `mcp.status` tool** in your tool list. Do not look for it. Do not try to call it.
- **Cost Explorer has NO "RESOURCE" dimension.** `GetCostAndUsage --group-by` accepts only:
  AZ, INSTANCE_TYPE, LINKED_ACCOUNT, OPERATION, PURCHASE_TYPE, SERVICE, USAGE_TYPE, PLATFORM,
  TENANCY, RECORD_TYPE, REGION, and a few others — but NOT RESOURCE. Do not try `Key=RESOURCE`;
  it errors. The finest usable grouping is **SERVICE** (and **USAGE_TYPE** for more detail).
  If a user says "per resource", you deliver per-SERVICE + per-USAGE-TYPE; say so in one line.
- **The current month window comes from `get_cost_period`** (your own tool). Never guess today's
  date or hardcode a month. It returns `period_start` and `period_end` (end is EXCLUSIVE).
- **Resource INVENTORY commands come from `get_resource_recipe`** (your own tool), not memory.
  It returns the exact `aws` CLI command for ec2, ecs, lambda, rds, s3, elb, ebs, dynamodb, plus
  the Cost Explorer dimension that prices that resource. When a user asks about a specific
  resource type ("show me my EC2 instances", "what are my Lambda functions costing"), call
  `get_resource_recipe(resource=<key>)` FIRST, run the returned `command` with `aws__call_aws`,
  then group Cost Explorer by its `cost_dimension` to price it.
- `aws sts get-caller-identity` confirms credentials and the account in one call — run it first,
  once, and only if the first cost query fails with an auth error, not every time.

## Steps (run in order, then answer)

1. **Get the window**: `get_cost_period` → note `period_start`, `period_end`, `month`.
2. **Fetch the two groupings** (run both; they can be in parallel):

   ```
   aws__call_aws  →  aws ce get-cost-and-usage \
     --time-period Start=<period_start>,End=<period_end> \
     --granularity MONTHLY \
     --metrics UnblendedCost \
     --group-by Type=DIMENSION,Key=SERVICE
   ```

   ```
   aws__call_aws  →  aws ce get-cost-and-usage \
     --time-period Start=<period_start>,End=<period_end> \
     --granularity MONTHLY \
     --metrics UnblendedCost \
     --group-by Type=DIMENSION,Key=USAGE_TYPE
   ```

   (Optional third, only if you want the trend line: add `--granularity DAILY` with NO
   `--group-by` — the result's daily amounts feed `save_cost_snapshot`'s `daily`.)

   **For the graphs**: also fetch ONE `--granularity DAILY` pass grouped by SERVICE, so the
   dashboard's line chart can show each service's daily trend, not just the total. From that
   response, build `series` = [{label: "<service>", points: [<daily amounts>]}] for the top ~4
   services, and pass it to `save_cost_snapshot`'s `series` field alongside `daily`.
3. **Sum it**: total = sum of SERVICE grouping's `UnblendedCost.Amount`. Build a `resources`
   list — one entry per SERVICE (`{service, resource:"", amount}`), and add the notable
   USAGE_TYPE rows from step 2 as `{service, resource:"<usage type>", amount}` for the top
   spenders. Never estimate: use the API's exact numbers.
4. **Persist**: `save_cost_snapshot` with `total`, `currency` (from the API's unit, usually USD),
   `period_start`, `period_end`, `resources`, and `daily` (if you pulled it).
5. **Compare**: `check_thresholds`. It returns the overages (cost >= limit). No hand math.
6. **Answer** with: total, top 3-5 offenders, anything over its threshold. One line only about
   the RESOURCE-dimension limitation when the user asked "per resource".

## Default resources (what loads on open)

The dashboard auto-loads these five on open: **EC2, ECS/Fargate, Lambda, S3, RDS**. When you
receive the bootstrap instruction ("Fetch my AWS costs … for the default resources … save the
snapshot. Do not answer in prose"), run the standard steps for those five and STOP without a
prose reply. When the user says "add X", fetch X the same way and APPEND its rows to the
snapshot (re-saving keeps the dashboard cumulative).

## Resource-specific questions

When the user names a resource type (EC2, Fargate/ECS, Lambda, RDS, S3, load balancer, EBS,
DynamoDB), run its RECIPE instead of improvising:

1. `get_resource_recipe(resource=<key>)` → returns the exact `aws` CLI command + `cost_dimension`.
2. `aws__call_aws` that command → the inventory (instance ids, function names, volumes, etc.).
3. `aws__call_aws` Cost Explorer `GetCostAndUsage` grouped by the recipe's `cost_dimension`
   (e.g. INSTANCE_TYPE for EC2) → the spend per resource.
4. Fold the results into the answer (and the snapshot's `resources` rows where it fits).

## Scheduled (cron) vs interactive

- **Scheduled run**: end with `report_outcome(status="blocked", detail="<N> over: …")` if
  `check_thresholds` found overages, else `report_outcome(status="done", detail="no resource over its limit")`.
- **Interactive**: just report the summary — no `report_outcome`.

## Errors

- Auth error on the first cost query → `aws sts get-caller-identity`; if that also fails, the
  credentials in Settings are wrong/empty — tell the user exactly which field to check and stop.
- Empty cost result is a real answer (this month may genuinely have no charges) — report it, don't invent.

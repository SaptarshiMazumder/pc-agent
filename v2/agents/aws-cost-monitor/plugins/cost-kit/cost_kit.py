"""cost-kit — the AWS Cost Monitor's OWN tools (agent-private plugin).

Pure LOCAL logic: it stores the last cost snapshot and the per-resource thresholds as JSON in
this agent's workspace, compares them, and shapes the dashboard view. It makes NO network calls
and reads NO secrets — the actual AWS data comes in through the `aws` MCP server, and this plugin
only remembers and compares what that server returned.

Workspace: `current_workspace()` gives this agent's working dir (agents/<id>/workspace/) in BOTH
a chat turn and a direct dashboard `tools.invoke` — the gateway sets the run context for both.
State lives under <workspace>/cost/ so it is never packaged.
"""

from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_run_context, current_workspace

# The daemon's state dir, injected at register time (from PluginContext.config). Used to resolve
# the ACCOUNT-scoped workspace (see _cost_dir) the same way the gateway's chat path does.
_STATE_DIR: Path | None = None


def _cost_dir() -> Path | None:
    """Where this agent's cost state lives.

    CRITICAL: the chat run and a direct dashboard `tools.invoke` resolve "workspace" DIFFERENTLY
    under account mode — chat uses the account-scoped dir, tools.invoke uses the plain agent dir.
    If this helper only trusted `current_workspace()`, the dashboard would read/write a DIFFERENT
    folder than the chat that just saved the snapshot (the bug where the agent says "added to the
    dashboard" and the board stays empty). So when an account is active, resolve the SAME
    account-scoped path the gateway uses (`user_state.account_workspace`), which is idempotent in
    both call paths.
    """
    from agent_runtime.infrastructure import accounts, user_state

    ctx = current_run_context()
    agent_id = getattr(ctx, "agent_id", "") if ctx is not None else ""
    acct = accounts.account_id()
    if acct and _STATE_DIR:
        base = user_state.account_workspace(_STATE_DIR, acct, agent_id)
    else:
        ws = current_workspace()
        base = Path(ws) if ws else None
    if base is None:
        return None
    d = Path(base) / "cost"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return d


def _read_json(path: Path, default):
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    return default


def _write_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _snapshot_path(d: Path) -> Path:
    return d / "snapshot.json"


def _thresholds_path(d: Path) -> Path:
    return d / "thresholds.json"


class GetCostPeriodTool(Tool):
    name = "get_cost_period"
    plugin = "cost-kit"
    label = "Cost period"
    concurrency = "parallel"
    # Deterministic, read-only — the agent calls it with the same (empty) args to learn the
    # current month window, so a human or a repeated turn is never mistaken for a loop.
    default_loop_max_repeats = 0
    description = (
        "Return the exact month-to-date time window to pass to Cost Explorer, so you never have "
        "to guess today's date. Returns {today, period_start, period_end, month}. Use "
        "period_start and period_end (end is EXCLUSIVE) as --time-period Start=…,End=… ."
    )
    parameters = {"type": "object", "properties": {}}

    async def execute(self, tool_call_id, params, abort, on_update=None):
        today = date.today()
        period_start = today.replace(day=1)
        # Cost Explorer's End is exclusive, so end = tomorrow includes today.
        period_end = today + timedelta(days=1)
        out = {
            "today": today.isoformat(),
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "month": today.strftime("%Y-%m"),
        }
        return ToolResult.text(json.dumps(out), details=out)


# Cost Explorer group-by dimension that attributes spend to that resource type.
_RECIPES = {
    "ec2": {
        "resource": "EC2 instances",
        "service": "Amazon EC2",
        "command": 'aws ec2 describe-instances --query "Reservations[*].Instances[*].[InstanceId,InstanceType,State.Name,LaunchTime]" --output json',
        "cost_dimension": "INSTANCE_TYPE",
        "note": "Lists running/stopped instances + type + launch time. Cost via Cost Explorer grouped by INSTANCE_TYPE.",
    },
    "ecs": {
        "resource": "ECS / Fargate",
        "service": "Amazon Elastic Container Service",
        "command": 'aws ecs list-clusters --output json && aws ecs list-services --cluster <CLUSTER> --output json',
        "cost_dimension": "USAGE_TYPE",
        "note": "Fargate cost is vCPU-hours + GB-hours (USAGE_TYPE Fargate-vCPU-Hours / Fargate-GB-Hours).",
    },
    "lambda": {
        "resource": "Lambda functions",
        "service": "AWS Lambda",
        "command": 'aws lambda list-functions --query "Functions[*].[FunctionName,Runtime,MemorySize]" --output json',
        "cost_dimension": "USAGE_TYPE",
        "note": "Lambda cost is requests + GB-seconds (USAGE_TYPE Lambda-GB-Second / Requests).",
    },
    "rds": {
        "resource": "RDS databases",
        "service": "Amazon Relational Database Service",
        "command": 'aws rds describe-db-instances --query "DBInstances[*].[DBInstanceIdentifier,DBInstanceClass,DBInstanceStatus]" --output json',
        "cost_dimension": "INSTANCE_TYPE",
        "note": "Lists DB instances + class + status.",
    },
    "s3": {
        "resource": "S3 buckets",
        "service": "Amazon Simple Storage Service",
        "command": 'aws s3api list-buckets --query "Buckets[*].Name" --output json',
        "cost_dimension": "USAGE_TYPE",
        "note": "S3 cost is storage GB-months + requests (USAGE_TYPE TimedStorage-ByteHrs / Requests-Tier*).",
    },
    "elb": {
        "resource": "Load balancers",
        "service": "Amazon Elastic Load Balancing",
        "command": 'aws elbv2 describe-load-balancers --query "LoadBalancers[*].[LoadBalancerName,Type,State.Code]" --output json',
        "cost_dimension": "USAGE_TYPE",
        "note": "ALB cost is LoadBalancerUsage hours + LCU-hours.",
    },
    "ebs": {
        "resource": "EBS volumes",
        "service": "Amazon Elastic Block Store",
        "command": 'aws ec2 describe-volumes --query "Volumes[*].[VolumeId,Size,State]" --output json',
        "cost_dimension": "USAGE_TYPE",
        "note": "EBS cost is provisioned GB-months + IOPS (USAGE_TYPE EBS:VolumeUsage.gp* etc.).",
    },
    "dynamodb": {
        "resource": "DynamoDB tables",
        "service": "Amazon DynamoDB",
        "command": 'aws dynamodb list-tables --output json',
        "cost_dimension": "USAGE_TYPE",
        "note": "DynamoDB cost is read/write request units + storage.",
    },
}


class GetResourceRecipeTool(Tool):
    name = "get_resource_recipe"
    plugin = "cost-kit"
    label = "Resource recipe"
    concurrency = "parallel"
    # Read-only lookup — exempt from loop guard (same args for a repeated question is expected).
    default_loop_max_repeats = 0
    description = (
        "Return the EXACT ready-made `aws` CLI command (and the Cost Explorer cost dimension) to "
        "fetch a major resource type's inventory/data. Pass `resource` = one of: ec2, ecs, lambda, "
        "rds, s3, elb, ebs, dynamodb (omit or pass 'all' for the full catalog). Run the returned "
        "`command` with aws__call_aws. Use this instead of composing aws commands from memory."
    )
    parameters = {
        "type": "object",
        "properties": {
            "resource": {
                "type": "string",
                "description": "Resource type key: ec2 | ecs | lambda | rds | s3 | elb | ebs | dynamodb | all.",
            }
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        key = str(params.get("resource") or "all").strip().lower()
        if key == "all":
            out = {"recipes": _RECIPES}
        elif key in _RECIPES:
            out = {"recipe": _RECIPES[key]}
        else:
            return ToolResult.text(
                f"unknown resource type '{key}'. Use one of: {', '.join(sorted(_RECIPES))} (or 'all').",
                is_error=True,
            )
        return ToolResult.text(json.dumps(out, ensure_ascii=False), details=out)


class SaveCostSnapshotTool(Tool):
    name = "save_cost_snapshot"
    plugin = "cost-kit"
    label = "Save cost snapshot"
    concurrency = "sequential"
    description = (
        "Persist the latest AWS cost picture so the dashboard and threshold checks can read it. "
        "Call this AFTER fetching costs from AWS. `total` is the period total (float), `currency` "
        "e.g. 'USD', `period_start`/`period_end` ISO dates or strings, `resources` a list of "
        "{service, resource, amount} (resource may be '' for service-level rows), and `daily` an "
        "optional list of {date, amount} for the trend line."
    )
    parameters = {
        "type": "object",
        "required": ["total", "currency", "period_start", "period_end", "resources"],
        "properties": {
            "total": {"type": "number", "description": "Total spend for the period."},
            "currency": {"type": "string", "description": "Currency code, e.g. USD."},
            "period_start": {"type": "string", "description": "Period start (ISO date or label)."},
            "period_end": {"type": "string", "description": "Period end (ISO date or label)."},
            "resources": {
                "type": "array",
                "description": "Per-resource cost rows.",
                "items": {
                    "type": "object",
                    "required": ["service", "amount"],
                    "properties": {
                        "service": {"type": "string"},
                        "resource": {"type": "string", "description": "Resource id/name; '' for a service-level row."},
                        "amount": {"type": "number"},
                    },
                },
            },
            "daily": {
                "type": "array",
                "description": "Daily totals for the trend line.",
                "items": {
                    "type": "object",
                    "required": ["date", "amount"],
                    "properties": {
                        "date": {"type": "string"},
                        "amount": {"type": "number"},
                    },
                },
            },
            "series": {
                "type": "array",
                "description": "Optional per-service daily series for the multi-line chart.",
                "items": {
                    "type": "object",
                    "required": ["label", "points"],
                    "properties": {
                        "label": {"type": "string"},
                        "points": {"type": "array", "items": {"type": "number"}},
                    },
                },
            },
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        d = _cost_dir()
        if d is None:
            return ToolResult.text(
                "could not locate this agent's workspace to store the snapshot", is_error=True
            )
        snap = {
            "total": float(params.get("total") or 0.0),
            "currency": str(params.get("currency") or "USD"),
            "period_start": str(params.get("period_start") or ""),
            "period_end": str(params.get("period_end") or ""),
            "resources": list(params.get("resources") or []),
            "daily": list(params.get("daily") or []),
            "series": list(params.get("series") or []),
        }
        try:
            _write_json(_snapshot_path(d), snap)
        except OSError as e:
            return ToolResult.text(f"failed to write snapshot: {e}", is_error=True)
        return ToolResult.text(
            f"saved cost snapshot: {snap['currency']} {snap['total']:.2f}, "
            f"{len(snap['resources'])} resource row(s)."
        )


class SetThresholdTool(Tool):
    name = "set_threshold"
    plugin = "cost-kit"
    label = "Set threshold"
    concurrency = "sequential"
    description = (
        "Set (or replace) the allowed-cost threshold for one resource or one service. Give "
        "`service` (e.g. 'Amazon EC2'), optionally `resource` (e.g. 'i-0abc123') to scope to a "
        "single resource, and `limit` (the dollar amount that triggers an alert). Omitting "
        "`resource` sets the limit for the whole service."
    )
    parameters = {
        "type": "object",
        "required": ["service", "limit"],
        "properties": {
            "service": {"type": "string", "description": "Service name, e.g. 'Amazon EC2'."},
            "resource": {"type": "string", "description": "Resource id/name; blank = whole service."},
            "limit": {"type": "number", "description": "Allowed cost in the period currency."},
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        d = _cost_dir()
        if d is None:
            return ToolResult.text("could not locate this agent's workspace", is_error=True)
        service = str(params.get("service") or "").strip()
        resource = str(params.get("resource") or "").strip()
        try:
            limit = float(params.get("limit"))
        except (TypeError, ValueError):
            return ToolResult.text("limit must be a number", is_error=True)
        if not service:
            return ToolResult.text("service is required", is_error=True)

        path = _thresholds_path(d)
        rows = _read_json(path, [])
        key = (service, resource)
        replaced = False
        for row in rows:
            if (row.get("service") == service and row.get("resource") == resource):
                row["limit"] = limit
                replaced = True
                break
        if not replaced:
            rows.append({"service": service, "resource": resource, "limit": limit})
        try:
            _write_json(path, rows)
        except OSError as e:
            return ToolResult.text(f"failed to write thresholds: {e}", is_error=True)
        scope = resource or service
        return ToolResult.text(f"threshold set: {scope} → {limit:.2f} (alert when cost reaches it).")


class ListThresholdsTool(Tool):
    name = "list_thresholds"
    plugin = "cost-kit"
    label = "List thresholds"
    concurrency = "parallel"
    # Read-only, no-arg — exempt from loop guard (same args every call is expected).
    default_loop_max_repeats = 0
    description = "List every allowed-cost threshold currently set."
    parameters = {"type": "object", "properties": {}}

    async def execute(self, tool_call_id, params, abort, on_update=None):
        d = _cost_dir()
        if d is None:
            return ToolResult.text("could not locate this agent's workspace", is_error=True)
        rows = _read_json(_thresholds_path(d), [])
        if not rows:
            return ToolResult.text("No thresholds set yet. Use set_threshold to add one.")
        lines = [
            f"- {(r.get('resource') or r.get('service'))}: {float(r.get('limit', 0)):.2f}"
            for r in rows
        ]
        return ToolResult.text("Thresholds:\n" + "\n".join(lines), details=rows)


class CheckThresholdsTool(Tool):
    name = "check_thresholds"
    plugin = "cost-kit"
    label = "Check thresholds"
    concurrency = "parallel"
    # Read-only, no-arg — exempt from loop guard (same args every call is expected).
    default_loop_max_repeats = 0
    description = (
        "Compare the LATEST saved cost snapshot against the set thresholds and return every "
        "resource/service whose cost has reached or crossed its allowed limit. Empty result means "
        "nothing is over. Use this (not hand comparison) to decide whether to alert."
    )
    parameters = {"type": "object", "properties": {}}

    async def execute(self, tool_call_id, params, abort, on_update=None):
        d = _cost_dir()
        if d is None:
            return ToolResult.text("could not locate this agent's workspace", is_error=True)
        snap = _read_json(_snapshot_path(d), None)
        if snap is None:
            return ToolResult.text(
                "no cost snapshot saved yet — fetch costs from AWS and call save_cost_snapshot first",
                is_error=True,
            )
        thresholds = _read_json(_thresholds_path(d), [])
        resources = snap.get("resources") or []
        currency = snap.get("currency", "USD")
        overages = []
        for thr in thresholds:
            service = thr.get("service") or ""
            resource = thr.get("resource") or ""
            try:
                limit = float(thr.get("limit", 0))
            except (TypeError, ValueError):
                continue
            if resource:
                # match one exact resource row
                match = next(
                    (
                        r
                        for r in resources
                        if r.get("service") == service and r.get("resource") == resource
                    ),
                    None,
                )
                cost = float(match.get("amount", 0)) if match else 0.0
            else:
                cost = sum(
                    float(r.get("amount", 0)) for r in resources if r.get("service") == service
                )
            if cost >= limit:
                overages.append(
                    {
                        "service": service,
                        "resource": resource,
                        "cost": round(cost, 2),
                        "limit": round(limit, 2),
                        "over_by": round(cost - limit, 2),
                    }
                )
        overages.sort(key=lambda o: -o["over_by"])
        if not overages:
            total = float(snap.get("total", 0) or 0)
            return ToolResult.text(
                f"No resource is over its limit. Current period total: {currency} {total:.2f}.",
                details={"overages": []},
            )
        lines = ["Resources over their allowed-cost threshold:"]
        for o in overages:
            scope = o["resource"] or o["service"]
            lines.append(
                f"- {scope}: {currency} {o['cost']:.2f} (limit {currency} {o['limit']:.2f}, "
                f"over by {currency} {o['over_by']:.2f})"
            )
        return ToolResult.text("\n".join(lines), details={"overages": overages})


class GetCostDashboardTool(Tool):
    name = "get_cost_dashboard"
    plugin = "cost-kit"
    label = "Cost dashboard"
    concurrency = "parallel"
    # Read-only + idempotent, and the dashboard's Refresh button calls it with the SAME empty
    # arguments every time (plus app.js reloads the board after each chat tool). The loop guard
    # would otherwise treat a few human clicks as a stuck loop and block it — opt out.
    default_loop_max_repeats = 0
    description = (
        "Return the dashboard view as JSON: top tiles, a daily trend series, and a per-resource "
        "table (with each resource's threshold and over/under state). Reads only stored data — "
        "the board calls this directly on Refresh, no AWS query, no model, no tokens."
    )
    parameters = {"type": "object", "properties": {}}

    async def execute(self, tool_call_id, params, abort, on_update=None):
        d = _cost_dir()
        if d is None:
            return ToolResult.text(
                json.dumps({"tiles": [], "series": [], "rows": {"columns": [], "data": []},
                            "note": "no workspace"}),
            )
        snap = _read_json(_snapshot_path(d), None)
        thresholds = _read_json(_thresholds_path(d), [])
        if snap is None:
            return ToolResult.text(
                json.dumps({
                    "tiles": [],
                    "series": [],
                    "bars": [],
                    "rows": {"columns": [], "data": []},
                    "note": "No cost data yet — it loads automatically on open (or press Refresh).",
                })
            )

        currency = snap.get("currency", "USD")
        total = float(snap.get("total", 0) or 0)
        resources = snap.get("resources") or []

        def limit_for(r):
            s = r.get("service") or ""
            res = r.get("resource") or ""
            for t in thresholds:
                if t.get("service") == s and t.get("resource") == res:
                    return float(t.get("limit", 0))
            for t in thresholds:
                if t.get("service") == s and not t.get("resource"):
                    return float(t.get("limit", 0))
            return None

        over_count = 0
        thresholded = 0
        for r in resources:
            lim = limit_for(r)
            if lim is not None:
                thresholded += 1
                if float(r.get("amount", 0) or 0) >= lim:
                    over_count += 1

        tiles = [
            {"label": "Period total", "value": f"{currency} {total:,.2f}"},
            {"label": "Resources tracked", "value": str(len(resources))},
            {"label": "Over limit", "value": str(over_count), "delta": "" if over_count == 0 else "needs attention"},
        ]

        daily = snap.get("daily") or []
        # line chart: the total daily trend, PLUS per-service daily lines when they were stored
        series = [{"label": "total", "points": [float(x.get("amount", 0)) for x in daily]}]
        for s in (snap.get("series") or []):
            if isinstance(s, dict) and (s.get("points") or []):
                series.append({"label": str(s.get("label") or "series"), "points": s["points"]})

        # bar chart: current cost by SERVICE (top N), for a quick ranking view
        by_service: dict[str, float] = {}
        for r in resources:
            if not (r.get("resource") or ""):  # service-level rows only
                by_service[str(r.get("service") or "other")] = float(r.get("amount", 0) or 0)
        bars = [
            {"label": k, "value": v}
            for k, v in sorted(by_service.items(), key=lambda kv: -kv[1])[:8]
            if v > 0
        ]

        # resource table, biggest spend first, with threshold + state
        sorted_res = sorted(resources, key=lambda r: -float(r.get("amount", 0) or 0))
        rows_data = []
        for r in sorted_res:
            amt = float(r.get("amount", 0) or 0)
            lim = limit_for(r)
            if lim is None:
                state, limit_txt = "—", "—"
            elif amt >= lim:
                state, limit_txt = "OVER", f"{currency} {lim:,.2f}"
            else:
                state, limit_txt = "ok", f"{currency} {lim:,.2f}"
            rows_data.append([
                r.get("service") or "",
                r.get("resource") or "",
                f"{currency} {amt:,.2f}",
                limit_txt,
                state,
            ])

        note = f"{snap.get('period_start','')} → {snap.get('period_end','')}"
        if over_count:
            note += f"  ·  {over_count} resource(s) OVER limit"

        return ToolResult.text(json.dumps({
            "tiles": tiles,
            "series": series,
            "bars": bars,
            "rows": {"columns": ["Service", "Resource", "Cost", "Limit", "State"], "data": rows_data},
            "note": note,
        }, ensure_ascii=False))


def register(api, ctx):
    global _STATE_DIR
    try:
        _STATE_DIR = Path(getattr(ctx.config, "state_dir", None) or "") or None
    except Exception:
        _STATE_DIR = None
    api.register_tool(GetCostPeriodTool())
    api.register_tool(GetResourceRecipeTool())
    api.register_tool(SaveCostSnapshotTool())
    api.register_tool(SetThresholdTool())
    api.register_tool(ListThresholdsTool())
    api.register_tool(CheckThresholdsTool())
    api.register_tool(GetCostDashboardTool())

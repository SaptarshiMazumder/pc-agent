# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARDS (plan item 3.6) — two of them, because they answer different questions for
# different moods.
#
#   SERVICE HEALTH  "is it working and is it fast" — opened during an incident.
#   BUSINESS        "are we making money" — opened daily.
#
# WHY THESE EXIST WHEN alarms + QUERIES.md ALREADY DO. Alarms tell you the moment something
# breaks; they cannot tell you what normal looks like, so you cannot tune a threshold from them.
# Logs Insights can answer anything, but only if you already know the metric name is the field
# key and that run_id is a property rather than a dimension. A dashboard is the thing you can
# look at without knowing any of that.
#
# ONLY ROLLUP DIMENSIONS ARE USED HERE. Same coupling as alarms.tf: `agentd_telemetry` publishes
# {service} and {service, outcome} alongside each call site's exact set, and those rollups are the
# only combinations guaranteed to exist. Graphing a dimension outside them yields a widget that is
# permanently, silently empty — which on a dashboard reads as "zero traffic", not "wrong query".
# ─────────────────────────────────────────────────────────────────────────────

locals {
  dash_period = 300

  # Widget grid is 24 columns wide. Kept as names so a layout change is one edit, not twelve.
  w_half    = 12
  w_third   = 8
  w_quarter = 6
  h_row     = 6
}

resource "aws_cloudwatch_dashboard" "service_health" {
  dashboard_name = "${local.name_prefix}-service-health"

  dashboard_body = jsonencode({
    widgets = [
      {
        type = "text", x = 0, y = 0, width = 24, height = 2
        properties = {
          markdown = <<-EOT
            ## ${local.name_prefix} · service health
            Is it working, and is it fast. **Money lives on the `${local.name_prefix}-business` dashboard.**
            402 (out of credits) and 403 (tier / not entitled) are the gate WORKING — they appear under Refusals, not Errors.
          EOT
        }
      },

      # --- traffic + errors ---------------------------------------------------
      {
        type = "metric", x = 0, y = 2, width = local.w_half, height = local.h_row
        properties = {
          title  = "Model calls"
          view   = "timeSeries", stacked = false, region = var.region
          period = local.dash_period, stat = "Sum"
          metrics = [
            [var.telemetry_namespace, "model_call_total", "service", "model-proxy", "outcome", "ok", { label = "ok" }],
          ]
        }
      },
      {
        type = "metric", x = local.w_half, y = 2, width = local.w_half, height = local.h_row
        properties = {
          title  = "Errors — anything here is a fault, not a refusal"
          view   = "timeSeries", stacked = true, region = var.region
          period = local.dash_period, stat = "Sum"
          metrics = [
            [var.telemetry_namespace, "ledger_write_total", "service", "model-proxy", "outcome", "fail", { label = "billing write failed" }],
            [".", "debit_applied_total", ".", ".", ".", "error", { label = "debit error" }],
            [".", "auth_total", ".", ".", ".", "unavailable", { label = "auth failing open" }],
            [".", "funding_lookup_total", ".", ".", ".", "unavailable", { label = "funding failing open" }],
            [".", "login_total", ".", "accounts", ".", "rejected", { label = "sign-in rejected" }],
          ]
          # A flat zero line is the desired state, so pin the axis: without this a single error
          # rescales the chart and looks alarming, while a hundred look identical to one.
          yAxis = { left = { min = 0 } }
        }
      },

      # --- latency ------------------------------------------------------------
      {
        type = "metric", x = 0, y = 8, width = local.w_half, height = local.h_row
        properties = {
          title = "Token check latency (runs before EVERY model call)"
          view  = "timeSeries", region = var.region, period = local.dash_period
          metrics = [
            [var.telemetry_namespace, "resolve_latency_ms", "service", "model-proxy", { stat = "p99", label = "p99" }],
            ["...", { stat = "p50", label = "p50" }],
          ]
          annotations = {
            horizontal = [{ label = "alarm threshold", value = var.resolve_latency_p99_ms }]
          }
        }
      },
      {
        type = "metric", x = local.w_half, y = 8, width = local.w_half, height = local.h_row
        properties = {
          title = "Load balancer — requests and 5xx"
          view  = "timeSeries", region = var.region, period = local.dash_period, stat = "Sum"
          metrics = [
            ["AWS/ApplicationELB", "RequestCount", "LoadBalancer", aws_lb.main.arn_suffix, { label = "requests" }],
            [".", "HTTPCode_Target_5XX_Count", ".", ".", { label = "5xx", color = "#d62728" }],
            [".", "HTTPCode_Target_4XX_Count", ".", ".", { label = "4xx (incl. the gate refusing)", color = "#ff7f0e" }],
          ]
        }
      },

      # --- per-service availability ------------------------------------------
      {
        type = "metric", x = 0, y = 14, width = local.w_half, height = local.h_row
        properties = {
          title = "Unhealthy targets — one task per service, so 1 IS an outage"
          view  = "timeSeries", region = var.region, period = 60, stat = "Maximum"
          metrics = [
            for name, _cfg in local.services : [
              "AWS/ApplicationELB", "UnHealthyHostCount",
              "TargetGroup", aws_lb_target_group.svc[name].arn_suffix,
              "LoadBalancer", aws_lb.main.arn_suffix,
              { label = name }
            ]
          ]
          yAxis = { left = { min = 0 } }
        }
      },
      {
        type = "metric", x = local.w_half, y = 14, width = local.w_half, height = local.h_row
        properties = {
          title  = "Auth cache hit rate — a collapse here makes accounts the bottleneck"
          view   = "timeSeries", stacked = true, region = var.region
          period = local.dash_period, stat = "Sum"
          metrics = [
            [var.telemetry_namespace, "auth_total", "service", "model-proxy", "outcome", "ok", { label = "resolved ok" }],
            [".", "resolve_total", ".", "accounts", ".", "ok", { label = "reached accounts (cache miss)" }],
          ]
        }
      },

      # --- the clocks (3.2) ---------------------------------------------------
      {
        type = "metric", x = 0, y = 20, width = 24, height = local.h_row
        properties = {
          title = "Speed as users feel it — HOSTED WEB ONLY (desktop daemons run on the user's PC and do not upload)"
          view  = "timeSeries", region = var.region, period = local.dash_period
          metrics = [
            [var.telemetry_namespace, "first_output_ms", "service", "daemon", { stat = "p50", label = "p50 to first output" }],
            ["...", { stat = "p95", label = "p95 to first output" }],
            [".", "first_text_ms", ".", ".", { stat = "p50", label = "p50 to first answer text" }],
            [".", "model_time_ms", ".", ".", { stat = "p50", label = "p50 model time per run" }],
            [".", "tool_duration_ms", ".", ".", { stat = "p50", label = "p50 per tool call" }],
          ]
        }
      },

      # --- what actually failed, in words ------------------------------------
      {
        type = "log", x = 0, y = 26, width = 24, height = local.h_row
        properties = {
          title  = "Recent failures (raw log lines)"
          region = var.region
          # Properties, not dimensions, are where the useful detail lives — model, account_id,
          # reason, status_code. That is precisely what a metric widget cannot show, which is
          # why a log widget belongs on this dashboard at all.
          query = join("\n", [
            "SOURCE '/${var.project}/${var.environment}'",
            "| fields @timestamp, service, outcome, reason, status_code, model, account_id",
            "| filter outcome in [\"fail\",\"error\",\"unavailable\",\"exception\",\"overspend\"]",
            "| sort @timestamp desc",
            "| limit 40",
          ])
        }
      },
    ]
  })
}

resource "aws_cloudwatch_dashboard" "business" {
  dashboard_name = "${local.name_prefix}-business"

  dashboard_body = jsonencode({
    widgets = [
      {
        type = "text", x = 0, y = 0, width = 24, height = 3
        properties = {
          markdown = <<-EOT
            ## ${local.name_prefix} · business health
            **Balance-sheet rows below need `POST /ledger/snapshot` on a schedule.** They are LEVELS
            (reserve left, creators owed, cost ratio) with no event to hang off, so nothing emits them
            unless something samples them. Empty widgets there mean the snapshot is not running — not zero.

            `ledger_balanced` must always read **1**. Anything else means a posting bypassed the ledger
            and every number on this page is suspect.
          EOT
        }
      },

      # --- the money coming in, split at the moment of sale -------------------
      {
        type = "metric", x = 0, y = 3, width = local.w_half, height = local.h_row
        properties = {
          title  = "Sales — gross, and where each dollar went"
          view   = "timeSeries", stacked = false, region = var.region
          period = 3600, stat = "Sum"
          metrics = [
            [var.telemetry_namespace, "purchase_gross_usd", "service", "accounts", { label = "gross USD" }],
            [".", "reserve_funded_usd", ".", ".", { label = "reserved for inference" }],
            [".", "creator_accrued_usd", ".", ".", { label = "accrued to creators" }],
          ]
        }
      },
      {
        type = "metric", x = local.w_half, y = 3, width = local.w_half, height = local.h_row
        properties = {
          title = "Provider spend (what inference actually cost us)"
          view  = "timeSeries", region = var.region, period = 3600, stat = "Sum"
          metrics = [
            [var.telemetry_namespace, "model_cost_usd", "service", "model-proxy", { label = "USD/hour" }],
            [".", "unbilled_cost_usd", ".", ".", { label = "UNBILLED — should be flat zero", color = "#d62728" }],
          ]
          annotations = {
            horizontal = [{ label = "cost/hour alarm", value = var.cost_per_hour_alarm_usd }]
          }
        }
      },

      # --- credits sold vs served --------------------------------------------
      {
        type = "metric", x = 0, y = 9, width = local.w_half, height = local.h_row
        properties = {
          title = "Credits sold vs consumed — selling faster than serving?"
          view  = "timeSeries", region = var.region, period = 3600, stat = "Sum"
          metrics = [
            [var.telemetry_namespace, "credits_granted_total", "service", "accounts", { label = "granted" }],
            [".", "credits_consumed_total", ".", ".", { label = "consumed" }],
            [".", "credits_expired_total", ".", ".", { label = "expired (breakage)" }],
          ]
        }
      },
      {
        type = "metric", x = local.w_half, y = 9, width = local.w_half, height = local.h_row
        properties = {
          title = "Tokens — cached share is the biggest lever on cost"
          view  = "timeSeries", stacked = true, region = var.region, period = 3600, stat = "Sum"
          metrics = [
            [var.telemetry_namespace, "tokens_total", "direction", "in", "service", "model-proxy", { label = "in" }],
            [".", ".", ".", "cached", ".", ".", { label = "cached (~10% price)" }],
            [".", ".", ".", "out", ".", ".", { label = "out" }],
          ]
        }
      },

      # --- the balance sheet, sampled ----------------------------------------
      {
        type = "metric", x = 0, y = 15, width = local.w_third, height = local.h_row
        properties = {
          title = "Reserve vs liability (needs /ledger/snapshot)"
          view  = "timeSeries", region = var.region, period = local.dash_period, stat = "Maximum"
          metrics = [
            [var.telemetry_namespace, "reserve_balance_usd", "service", "accounts", { label = "inference reserve" }],
            [".", "credit_liability_usd", ".", ".", { label = "credits owed as service" }],
            [".", "creator_payable_usd", ".", ".", { label = "owed to creators" }],
          ]
        }
      },
      {
        type = "metric", x = local.w_third, y = 15, width = local.w_third, height = local.h_row
        properties = {
          title = "Cost ratio — share of revenue going to providers"
          view  = "timeSeries", region = var.region, period = local.dash_period, stat = "Maximum"
          metrics = [
            [var.telemetry_namespace, "cogs_ratio", "service", "accounts", { label = "COGS ratio" }],
          ]
          # The markup's implied ratio. Drifting above it means we are selling below the price
          # the credit rate was sized for.
          annotations = {
            horizontal = [{ label = "target (1 / markup)", value = 0.30 }]
          }
          yAxis = { left = { min = 0, max = 1 } }
        }
      },
      {
        type = "metric", x = local.w_third * 2, y = 15, width = local.w_third, height = local.h_row
        properties = {
          title = "Gross margin + books balanced"
          view  = "singleValue", region = var.region, period = 86400, stat = "Maximum"
          metrics = [
            [var.telemetry_namespace, "gross_margin_usd", "service", "accounts", { label = "gross margin USD" }],
            [".", "ledger_balanced", ".", ".", { label = "balanced (must be 1)" }],
            [".", "credits_outstanding", ".", ".", { label = "credits outstanding" }],
          ]
        }
      },

      # --- demand signals ----------------------------------------------------
      {
        type = "metric", x = 0, y = 21, width = local.w_half, height = local.h_row
        properties = {
          title  = "Refusals — reliability signal AND the best conversion signal we have"
          view   = "timeSeries", stacked = true, region = var.region
          period = local.dash_period, stat = "Sum"
          metrics = [
            [var.telemetry_namespace, "run_refused_total", "reason", "no_credits", "service", "model-proxy", { label = "out of credits (402)" }],
            [".", ".", ".", "model_tier", ".", ".", { label = "model above tier (403)" }],
            [".", ".", ".", "not_entitled", ".", ".", { label = "no subscription (403)" }],
          ]
        }
      },
      {
        type = "metric", x = local.w_half, y = 21, width = local.w_half, height = local.h_row
        properties = {
          title = "Sign-ups, sign-ins, renewals"
          view  = "timeSeries", region = var.region, period = 3600, stat = "Sum"
          metrics = [
            [var.telemetry_namespace, "login_total", "service", "accounts", "outcome", "ok", { label = "sign-ins" }],
            [".", "purchase_total", ".", ".", ".", "ok", { label = "purchases" }],
            [".", "renewal_total", ".", ".", ".", "ok", { label = "renewals" }],
            [".", "renewal_total", ".", ".", ".", "failed", { label = "renewals FAILED", color = "#d62728" }],
          ]
        }
      },

      # --- who is spending ---------------------------------------------------
      {
        type = "log", x = 0, y = 27, width = 24, height = local.h_row
        properties = {
          title  = "Top spenders this period (account_id is a property, so only logs can group by it)"
          region = var.region
          query = join("\n", [
            "SOURCE '/${var.project}/${var.environment}'",
            "| filter ispresent(model_cost_usd)",
            "| stats sum(model_cost_usd) as cost_usd, sum(credits_charged_total) as credits by account_id",
            "| sort cost_usd desc",
            "| limit 20",
          ])
        }
      },
    ]
  })
}

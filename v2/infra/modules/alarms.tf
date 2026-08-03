# ─────────────────────────────────────────────────────────────────────────────
# ALARMS (plan item 3.5) — the counters Phase 0/1 emit are useless until something
# watches them. Everything Phase 1 protects against is SILENT: a failed billing write,
# an unattributed charge, a queued ledger row. The user's chat succeeds either way, so
# absent an alarm these are only ever noticed by someone running a script by hand.
#
# THREE NON-OBVIOUS THINGS DRIVE THE SHAPE OF THIS FILE.
#
# 1. EMF creates one metric per EXACT dimension set, and a call site is free to add a
#    dimension the next one omits: `count("ledger_write_total", outcome="fail",
#    reason="http_error")` publishes under {outcome, reason, service} while `outcome="ok"`
#    publishes under {outcome, service}. Those are DIFFERENT metrics, so there is no exact
#    set an alarm could name — and metric alarms CANNOT paper over that with SEARCH() the
#    way a dashboard can (PutMetricAlarm rejects it outright: "SEARCH is not supported on
#    Metric Alarms"), because an alarm must bind to a fixed set of time series.
#    The fix is on the EMIT side: agentd_telemetry publishes every metric under stable
#    ROLLUP dimension sets — {service} and {service, outcome} — in addition to the exact
#    set (see emf._dimension_sets). Those rollups are what the alarms below bind to, which
#    is why a plain `dimensions = { service = ..., outcome = "fail" }` matches every failure
#    regardless of which `reason` the call site tagged.
#    THE TWO FILES ARE COUPLED: changing AGENTD_TELEMETRY_ROLLUP_KEYS, or alarming on a
#    dimension outside those rollups, silently produces an alarm that can never fire.
#
# 2. For most of these, NO DATA IS THE HEALTHY STATE — nobody expects a steady trickle
#    of failed billing writes. Hence treat_missing_data = "notBreaching" throughout.
#    The two alarms where absence is the problem are called out individually.
#
# 3. `period` GOES ON EACH metric_query, NOT ON THE ALARM, for the one alarm that still
#    needs metric math (accounts_unreachable, which adds two metrics together). A math
#    alarm rejects the top-level `period` argument but CloudWatch still requires one per
#    query, and fails with a bare "Period must not be null" that names the alarm rather
#    than the missing field. Plain alarms take `period` at the top level as usual.
#
# ok_actions are set on the money alarms: knowing a money leak STOPPED matters as much
# as knowing it started.
# ─────────────────────────────────────────────────────────────────────────────

locals {
  # Metric-math alarms need the namespace inlined into a search string. See DEF-8 in the
  # plan: this is `agentd` for every environment today, which is fine while dev is the
  # only environment and must be split before a second one exists.
  ns = var.telemetry_namespace

  # One period for every rate-style alarm. 5 minutes is short enough to matter and long
  # enough that a single blip does not page.
  period = 300

  # The `service` dimension's value is AGENTD_SERVICE inside the container, which each
  # Dockerfile sets to the service's own name — the same keys as var.services. Named here
  # because every alarm below needs it, and because it is the one value that, if wrong,
  # produces alarms that install cleanly and never fire.
  svc_proxy    = "model-proxy"
  svc_accounts = "accounts"
}

# --- Where alerts go ----------------------------------------------------------------
# The topic always exists so alarms have a valid action target. The email subscription is
# conditional: an empty var.alert_email means "create the plumbing, do not subscribe" —
# useful before you have decided who is on call.
resource "aws_sns_topic" "alerts" {
  name = "${local.name_prefix}-alerts"
  tags = local.common_tags
}

resource "aws_sns_topic_subscription" "alerts_email" {
  count     = var.alert_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email

  # AWS emails a confirmation link that a human must click. Until then the subscription
  # is "PendingConfirmation" and alarms fire into the void, so Terraform reporting success
  # here does NOT mean alerts are reaching anyone. Verify with:
  #   aws sns list-subscriptions-by-topic --topic-arn <arn>
  lifecycle {
    ignore_changes = [endpoint]
  }
}

# ══════════════════════════════════════════════════════════════════════════════
# TIER 1 — money. Each of these means real dollars are being lost or mischarged.
# ══════════════════════════════════════════════════════════════════════════════

# Money paid to a provider that could not be attributed to any account. The single worst
# signal in the system: there is no other symptom, and it cannot be reconstructed later.
resource "aws_cloudwatch_metric_alarm" "unbilled_spend" {
  alarm_name          = "${local.name_prefix}-unbilled-spend"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
  tags                = local.common_tags

  alarm_description = <<-EOT
    Provider spend that could not be billed to any account. Money is leaving with nobody to
    charge, and the chat succeeded so no user will report it.

    Diagnose (Logs Insights, log group /${var.project}/${var.environment}):
      filter ispresent(unbilled_cost_usd) | stats sum(unbilled_cost_usd) as usd by model

    Usual cause: the request reached the proxy without an account (master key used for user
    traffic, or the accounts /resolve lookup failed open). Check auth_total{credential}.
  EOT

  # No `outcome` on this metric — it is emitted only when spend IS unattributed, so its
  # existence is the signal. {service} is the coarsest rollup and always present.
  namespace   = local.ns
  metric_name = "unbilled_cost_usd"
  statistic   = "Sum"
  period      = local.period
  dimensions  = { service = local.svc_proxy }
}

# A billing row did not land. DEF-1: this used to be swallowed entirely.
resource "aws_cloudwatch_metric_alarm" "ledger_write_failures" {
  alarm_name          = "${local.name_prefix}-ledger-write-failures"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
  tags                = local.common_tags

  alarm_description = <<-EOT
    Usage rows are failing to write to accounts. Spend continues; the record of it does not.

    Diagnose:
      filter ispresent(ledger_write_total) | stats sum(ledger_write_total) as n by outcome, reason

    reason=unreachable -> accounts is down or the internal key rotated without rolling the
    proxy. reason=http_error -> read status_code on the same lines.
  EOT

  # The {service, outcome} rollup is what makes this work: failures are tagged with a
  # `reason` too, and reasons multiply, so only the rollup covers all of them at once.
  namespace   = local.ns
  metric_name = "ledger_write_total"
  statistic   = "Sum"
  period      = local.period
  dimensions  = { service = local.svc_proxy, outcome = "fail" }
}

# Rows queued in the proxy's MEMORY. Every deploy replaces the task, so a non-zero depth
# is billing data with a deadline.
resource "aws_cloudwatch_metric_alarm" "ledger_buffer_backlog" {
  alarm_name          = "${local.name_prefix}-ledger-buffer-backlog"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
  tags                = local.common_tags

  alarm_description = <<-EOT
    Billing rows are buffered in the model-proxy's memory because accounts could not take
    them. They are replayed opportunistically, but the buffer is bounded and does not
    survive a task replacement -- which every deploy performs. DO NOT DEPLOY while this is
    firing; fix accounts first, confirm the buffer drains to 0, then deploy.

    Diagnose:
      filter ispresent(ledger_buffer_depth) | stats max(ledger_buffer_depth) by bin(5m)
  EOT

  # Maximum, not Sum: this is a level, and the question is whether the queue was EVER
  # non-empty in the window, not how many samples reported it.
  namespace   = local.ns
  metric_name = "ledger_buffer_depth"
  statistic   = "Maximum"
  period      = local.period
  dimensions  = { service = local.svc_proxy }
}

# The pre-call gate let through a call the balance could not cover. Measures how leaky the
# hard cap is, which is what makes worst-case cost knowable and same-day payouts safe.
resource "aws_cloudwatch_metric_alarm" "cap_overspend" {
  alarm_name          = "${local.name_prefix}-cap-overspend"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
  tags                = local.common_tags

  alarm_description = <<-EOT
    A model call completed that the account's balance could not pay for. Unrecoverable spend.

    Expected causes, both benign in small numbers: the funding cache window (a second call
    starting before the first debited), or one call costing more than the entire remaining
    balance. A sustained rate means the gate needs a per-call cost estimate, not just a
    balance check.

    Diagnose:
      filter ispresent(overspend_usd) | stats sum(overspend_usd) as usd, count(*) as n by model
  EOT

  namespace   = local.ns
  metric_name = "debit_applied_total"
  statistic   = "Sum"
  period      = local.period
  dimensions  = { service = local.svc_proxy, outcome = "overspend" }
}

# ══════════════════════════════════════════════════════════════════════════════
# TIER 1 — availability. Users are blocked, or the cap is not being enforced at all.
# ══════════════════════════════════════════════════════════════════════════════

# Both the auth path and the funding gate FAIL OPEN when accounts is unreachable: users
# keep working, unmetered and uncapped. That is the correct trade (an accounts blip must
# not take the product down) and precisely why it needs an alarm -- the failure is
# invisible from the outside while spend is unbounded.
resource "aws_cloudwatch_metric_alarm" "accounts_unreachable" {
  alarm_name          = "${local.name_prefix}-accounts-unreachable"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
  tags                = local.common_tags

  alarm_description = <<-EOT
    The model-proxy cannot reach accounts. Auth and the credit gate both fail OPEN, so
    traffic is currently flowing UNMETERED and UNCAPPED. Spend is unbounded until this
    clears, and the usage rows for this window may never be recoverable.

    Diagnose:
      filter ispresent(auth_total) or ispresent(funding_lookup_total)
      | stats count(*) as n by service, outcome

    Check the accounts service first (running count, target health), then whether
    ACCOUNTS_INTERNAL_KEY in Secrets Manager matches what the running tasks hold -- a
    rotated key without a service roll looks exactly like an outage.
  EOT

  # The one alarm here that still needs metric math — it adds two separate metrics. Both
  # bind to the {service, outcome} rollup; `auth_total` also carries credential/cache
  # dimensions at the call site, which the rollup is precisely what lets us ignore.
  metric_query {
    id    = "auth_unavailable"
    label = "auth lookups failing open"
    metric {
      namespace   = local.ns
      metric_name = "auth_total"
      stat        = "Sum"
      period      = local.period
      dimensions  = { service = local.svc_proxy, outcome = "unavailable" }
    }
  }

  metric_query {
    id    = "funding_unavailable"
    label = "funding lookups failing open"
    metric {
      namespace   = local.ns
      metric_name = "funding_lookup_total"
      stat        = "Sum"
      period      = local.period
      dimensions  = { service = local.svc_proxy, outcome = "unavailable" }
    }
  }

  # Either path failing means the same thing operationally, so one alarm covers both
  # rather than two that always fire together.
  metric_query {
    id          = "either"
    expression  = "SUM([FILL(auth_unavailable,0), FILL(funding_unavailable,0)])"
    label       = "accounts lookups failing open"
    return_data = true
  }
}

# No redundancy today: one task per service, so an unhealthy target IS an outage.
resource "aws_cloudwatch_metric_alarm" "unhealthy_targets" {
  for_each = local.services

  alarm_name          = "${local.name_prefix}-${each.key}-unhealthy"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "UnHealthyHostCount"
  statistic           = "Maximum"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  period              = 60
  evaluation_periods  = 2
  # notBreaching, not "breaching": a torn-down or scaled-to-zero dev environment reports no
  # data, and paging for a deliberate `desired_count = 0` trains people to ignore the alarm.
  treat_missing_data = "notBreaching"
  alarm_actions      = [aws_sns_topic.alerts.arn]
  ok_actions         = [aws_sns_topic.alerts.arn]
  tags               = local.common_tags

  alarm_description = "Target group for ${each.key} has an unhealthy target. Only one task runs per service, so this is a full outage of ${each.key}. Health path: ${each.value.health_path}"

  dimensions = {
    TargetGroup  = aws_lb_target_group.svc[each.key].arn_suffix
    LoadBalancer = aws_lb.main.arn_suffix
  }
}

# Users cannot chat. An absolute count rather than the 1%-of-requests ratio the plan
# sketched: at dev traffic levels a ratio means one error in three requests trips a 33%
# rate, so the percentage is noise until there is real volume. Revisit with traffic.
resource "aws_cloudwatch_metric_alarm" "proxy_5xx" {
  alarm_name          = "${local.name_prefix}-model-proxy-5xx"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_Target_5XX_Count"
  statistic           = "Sum"
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.proxy_5xx_threshold
  period              = local.period
  evaluation_periods  = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  tags                = local.common_tags

  alarm_description = <<-EOT
    The model-proxy is returning server errors; users cannot chat.

    NOTE 402 (out of credits) and 403 (model above plan tier) are 4xx and deliberately do
    NOT count here -- those are the gate working. Look for them via run_refused_total.
  EOT

  dimensions = {
    TargetGroup  = aws_lb_target_group.svc["model-proxy"].arn_suffix
    LoadBalancer = aws_lb.main.arn_suffix
  }
}

# ══════════════════════════════════════════════════════════════════════════════
# TIER 2 — spend rate and latency. Not an outage; still wants a human.
# ══════════════════════════════════════════════════════════════════════════════

# Runaway spend: a loop, an abusive account, or a pricing mistake. Hourly window because
# cost is only meaningful as a rate.
resource "aws_cloudwatch_metric_alarm" "cost_per_hour" {
  alarm_name          = "${local.name_prefix}-cost-per-hour"
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.cost_per_hour_alarm_usd
  evaluation_periods  = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  tags                = local.common_tags

  alarm_description = <<-EOT
    Provider spend exceeded $${var.cost_per_hour_alarm_usd}/hour.

    Find who:
      filter ispresent(model_cost_usd)
      | stats sum(model_cost_usd) as usd by account_id | sort usd desc

    Then which model, and whether one run is looping:
      filter ispresent(model_cost_usd)
      | stats sum(model_cost_usd) as usd, sum(model_call_total) as calls by run_id
      | sort usd desc
  EOT

  # Hourly period: cost is only meaningful as a rate, and the threshold is USD/hour.
  namespace   = local.ns
  metric_name = "model_cost_usd"
  statistic   = "Sum"
  period      = 3600
  dimensions  = { service = local.svc_proxy }
}

# Token resolution sits in front of EVERY model call for EVERY user, so its tail latency
# is the whole platform's floor.
resource "aws_cloudwatch_metric_alarm" "resolve_latency" {
  alarm_name          = "${local.name_prefix}-resolve-latency-p99"
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.resolve_latency_p99_ms
  evaluation_periods  = 2
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  tags                = local.common_tags

  alarm_description = <<-EOT
    p99 session-token resolution is slow. This runs before every model call, so it is added
    latency for every user and it makes accounts a bottleneck on the hot path (DEF-6).

    First check the resolve cache hit rate -- a collapsed hit rate multiplies load on
    accounts:
      filter ispresent(auth_total) | stats count(*) as n by cache, outcome

    Password hashing is deliberately slow (PBKDF2, 200k rounds) but that is /login, not
    /resolve. If login_ms moved too, someone changed the rounds.
  EOT

  # {service} rather than {service, outcome}: the tail latency users feel includes the
  # slow failures, so this is deliberately p99 across ALL outcomes. That is the coarsest
  # rollup, which exists precisely so an "everything" question stays answerable.
  namespace          = local.ns
  metric_name        = "resolve_latency_ms"
  extended_statistic = "p99"
  period             = local.period
  dimensions         = { service = local.svc_proxy }
}

# Credential stuffing, or a broken sign-in path rejecting valid passwords. Both show up
# here first, and both matter: if nobody can sign in, nothing else about the platform does.
resource "aws_cloudwatch_metric_alarm" "login_rejections" {
  alarm_name          = "${local.name_prefix}-login-rejections"
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.login_rejection_threshold
  evaluation_periods  = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  tags                = local.common_tags

  alarm_description = <<-EOT
    Unusual rate of rejected sign-ins. Either an attack, or a change that broke password
    verification for legitimate users -- distinguish by whether login_total{outcome=ok}
    also went to zero:
      filter ispresent(login_total) | stats count(*) as n by outcome, bin(5m)
  EOT

  # Emitted by ACCOUNTS, not the proxy — sign-in never touches the model path.
  namespace   = local.ns
  metric_name = "login_total"
  statistic   = "Sum"
  period      = local.period
  dimensions  = { service = local.svc_accounts, outcome = "rejected" }
}

# THE ONE ALARM WHERE ABSENCE IS THE FAILURE, hence treat_missing_data = "breaching".
# Off by default: a dev environment with no users is silent for hours at a time and this
# would page all night for nothing. Turn it on once real traffic makes silence abnormal.
resource "aws_cloudwatch_metric_alarm" "no_successful_logins" {
  count = var.enable_login_absence_alarm ? 1 : 0

  alarm_name          = "${local.name_prefix}-no-successful-logins"
  comparison_operator = "LessThanThreshold"
  threshold           = 1
  evaluation_periods  = 1
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
  tags                = local.common_tags

  alarm_description = <<-EOT
    No successful sign-in in ${var.login_absence_window_minutes} minutes. Enable only where
    continuous traffic makes silence genuinely abnormal, otherwise it pages for quiet nights.

    This alarm treats MISSING DATA AS BREACHING, unlike every other alarm here -- the point
    is to catch total absence, which is what a fully broken sign-in looks like.
  EOT

  namespace   = local.ns
  metric_name = "login_total"
  statistic   = "Sum"
  period      = var.login_absence_window_minutes * 60
  dimensions  = { service = local.svc_accounts, outcome = "ok" }
}

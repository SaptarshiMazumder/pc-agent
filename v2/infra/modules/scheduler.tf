# ─────────────────────────────────────────────────────────────────────────────
# THE CLOCK (plan item 3.8) — three accounts endpoints that no user request will
# ever trigger, and what runs them.
#
#   /ledger/snapshot        publishes the balance sheet as CloudWatch metrics
#   /subscriptions/renew-due charges subscriptions whose period ended
#   /ledger/close-expired   books breakage for credits that expired unspent
#
# WHY A CLOCK AND NOT A THREAD IN THE APP. A timer inside the accounts container fires
# once PER REPLICA the moment there is more than one — billing every subscriber twice.
# An external schedule calling an endpoint is a thing you can see, retry, disable, and
# invoke by hand. (accounts/app.py says the same thing from the other side.)
#
# WHY A LAMBDA IN BETWEEN. Scheduler's targets are AWS APIs; it has no HTTP target, and
# EventBridge API destinations require HTTPS while this ALB is HTTP-only for now. So a
# ~60-line function relays clock → HTTP. It holds no business logic: the endpoint comes
# from the schedule's input payload, so a new job is a change to var.scheduled_jobs.
#
# COST, WHICH IS THE WHOLE REASON THE DEFAULT CADENCES LOOK CONSERVATIVE.
# CloudWatch bills custom metrics per metric PER MONTH, prorated by the hour — NOT per
# datapoint. /ledger/snapshot emits 11 gauges, so running it every 5 minutes keeps 11
# metrics alive for all 720 hours of the month (11 × $0.30 ≈ $3.30/mo) while running it
# once a day touches ~30 hours (≈ $0.14/mo) and draws the same line at daily resolution.
# The interval WITHIN an hour is free; the number of hours and the number of metric names
# are the only levers. A balance sheet does not move between purchases, so daily is the
# honest default at low volume — raise `ledger-snapshot` to rate(5 minutes) when real
# purchases make the resolution worth $3/mo.
#
# Scheduler invocations (~9k/mo) and Lambda (~9k invocations) are under a cent combined.
# ─────────────────────────────────────────────────────────────────────────────

# ── The function ───────────────────────────────────────────────────────────────

# Zipped from source at plan time. `output_path` deliberately lives under .terraform/ so
# the artefact is never committed.
data "archive_file" "scheduled_jobs" {
  type        = "zip"
  source_file = "${path.module}/lambda/scheduled_jobs.py"
  output_path = "${path.module}/.terraform/scheduled_jobs.zip"
}

# Created explicitly rather than left to Lambda: a log group Lambda creates for itself has
# NO retention policy, so every line it ever writes is stored forever at $0.03/GB/month.
resource "aws_cloudwatch_log_group" "scheduled_jobs" {
  name              = "/aws/lambda/${local.name_prefix}-scheduled-jobs"
  retention_in_days = 14
  tags              = local.common_tags
}

resource "aws_iam_role" "scheduled_jobs" {
  name = "${local.name_prefix}-scheduled-jobs"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
  tags = local.common_tags
}

# Writing logs + creating the ENI that puts the function in the VPC. Both are AWS-managed
# and neither grants access to anything of ours.
resource "aws_iam_role_policy_attachment" "scheduled_jobs_vpc" {
  role       = aws_iam_role.scheduled_jobs.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

# The function's own security group. It could have reused the service SG (whose self-rule
# already permits this traffic), but a distinct group is what makes "the scheduler may talk
# to accounts" visible in the console instead of implied.
resource "aws_security_group" "scheduled_jobs" {
  name        = "${local.name_prefix}-scheduled-jobs"
  description = "Scheduled-jobs Lambda: outbound to the services only"
  vpc_id      = aws_vpc.main.id
  tags        = merge(local.common_tags, { Name = "${local.name_prefix}-scheduled-jobs" })
}

resource "aws_vpc_security_group_egress_rule" "scheduled_jobs_all" {
  security_group_id = aws_security_group.scheduled_jobs.id
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_ingress_rule" "svc_from_scheduled_jobs" {
  security_group_id            = aws_security_group.service.id
  description                  = "accounts from the scheduled-jobs Lambda"
  ip_protocol                  = "tcp"
  from_port                    = local.services["accounts"].port
  to_port                      = local.services["accounts"].port
  referenced_security_group_id = aws_security_group.scheduled_jobs.id
}

resource "aws_lambda_function" "scheduled_jobs" {
  function_name    = "${local.name_prefix}-scheduled-jobs"
  role             = aws_iam_role.scheduled_jobs.arn
  runtime          = "python3.12"
  handler          = "scheduled_jobs.handler"
  filename         = data.archive_file.scheduled_jobs.output_path
  source_code_hash = data.archive_file.scheduled_jobs.output_base64sha256
  # Generous because it waits on a Fargate container that may be cold, and because
  # renew-due does real work per subscription. It never runs concurrently with itself in
  # practice, so a long timeout costs nothing.
  timeout     = var.scheduled_job_timeout_seconds
  memory_size = 128
  description = "Runs the accounts service's scheduled endpoints (ledger snapshot, renewals, breakage)."

  # In the VPC on purpose: this lets the function call accounts by its private
  # service-discovery name, so the internal key never travels over the public internet.
  # The cost is no internet route (public subnets, no NAT), which is why the key is an env
  # var rather than a Secrets Manager read — see the handler's header for that trade.
  vpc_config {
    subnet_ids         = aws_subnet.public[*].id
    security_group_ids = [aws_security_group.scheduled_jobs.id]
  }

  environment {
    variables = {
      ACCOUNTS_URL            = "http://accounts.${var.project}.local:${local.services["accounts"].port}"
      ACCOUNTS_INTERNAL_KEY   = random_password.accounts_internal_key.result
      REQUEST_TIMEOUT_SECONDS = tostring(var.scheduled_job_timeout_seconds - 5)
    }
  }

  # Without this the function can start before the retention policy exists, and Lambda
  # creates the never-expiring log group first.
  depends_on = [aws_cloudwatch_log_group.scheduled_jobs]

  tags = local.common_tags
}

# ── The schedules ──────────────────────────────────────────────────────────────

resource "aws_iam_role" "scheduler" {
  name = "${local.name_prefix}-scheduler"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "scheduler.amazonaws.com" }
      # Scheduler is a shared AWS service; without this any other account's schedule could
      # name this role in a target. Standard confused-deputy guard.
      Condition = { StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id } }
    }]
  })
  tags = local.common_tags
}

resource "aws_iam_role_policy" "scheduler_invoke" {
  name = "invoke-scheduled-jobs"
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["lambda:InvokeFunction"]
      Resource = [aws_lambda_function.scheduled_jobs.arn]
    }]
  })
}

resource "aws_scheduler_schedule" "job" {
  for_each = var.scheduled_jobs

  name        = "${local.name_prefix}-${each.key}"
  description = each.value.description
  state       = each.value.enabled ? "ENABLED" : "DISABLED"

  schedule_expression          = each.value.schedule
  schedule_expression_timezone = var.scheduled_job_timezone

  # OFF = run at the stated minute. The flexible window exists to spread load across a
  # fleet; here the schedules are deliberately staggered by hand (see var.scheduled_jobs)
  # so that close-expired lands before the snapshot that reports it.
  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.scheduled_jobs.arn
    role_arn = aws_iam_role.scheduler.arn

    # The whole job definition, as data. The function reads `path` and calls it.
    input = jsonencode({ job = each.key, path = each.value.path })

    # Safe ONLY because all three endpoints are idempotent: renewals key on
    # subscription+period, breakage keys per grant, the snapshot is a pure read. A retry
    # that lands after a response was lost repeats no money movement.
    retry_policy {
      maximum_retry_attempts       = 2
      maximum_event_age_in_seconds = 300
    }
  }
}

# ── Watching it ────────────────────────────────────────────────────────────────

# Any unhandled exception in the handler — non-2xx, DNS failure, timeout, rotated key —
# increments AWS/Lambda Errors. Vendor metrics are free, which is why this alarm carries no
# custom-metric cost even though it covers every failure mode the function has.
resource "aws_cloudwatch_metric_alarm" "scheduled_jobs_failing" {
  alarm_name          = "${local.name_prefix}-scheduled-jobs-failing"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
  tags                = local.common_tags

  alarm_description = <<-EOT
    A scheduled accounts job failed. What that costs depends on which one:
      subscription-renewals -> subscribers are not being charged (revenue stops silently)
      close-expired-credits -> expired credits stay on the books as a liability
      ledger-snapshot       -> the business dashboard's balance-sheet rows go flat

    Read the reason: log group ${aws_cloudwatch_log_group.scheduled_jobs.name}
      fields @timestamp, job, outcome, status, detail | filter outcome != "ok" | sort @timestamp desc

    Reproduce it by hand (same payload the schedule sends):
      aws lambda invoke --function-name ${local.name_prefix}-scheduled-jobs \
        --payload '{"job":"manual","path":"/ledger/snapshot"}' --cli-binary-format raw-in-base64-out out.json
  EOT

  namespace   = "AWS/Lambda"
  metric_name = "Errors"
  statistic   = "Sum"
  period      = local.period
  dimensions  = { FunctionName = aws_lambda_function.scheduled_jobs.function_name }
}

# The opposite failure: nothing fails because nothing RUNS. A deleted or disabled schedule,
# or a Scheduler role that lost its permission, produces silence — and silence is the one
# state `notBreaching` cannot distinguish from health, so this alarm treats missing data as
# breaching. Off by default because it necessarily fires once in the window BEFORE the
# first invocation; turn it on after `scheduler_check.ps1` passes.
resource "aws_cloudwatch_metric_alarm" "scheduled_jobs_absent" {
  count = var.enable_job_absence_alarm ? 1 : 0

  alarm_name          = "${local.name_prefix}-scheduled-jobs-not-running"
  comparison_operator = "LessThanThreshold"
  threshold           = 1
  evaluation_periods  = 1
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
  tags                = local.common_tags

  alarm_description = <<-EOT
    No scheduled job ran in the last 24 hours. The billing clock has stopped: renewals are
    not being charged and breakage is not being booked, with no error anywhere because
    nothing is executing.

    Check the schedules still exist and are ENABLED:
      aws scheduler list-schedules --name-prefix ${local.name_prefix}
  EOT

  namespace   = "AWS/Lambda"
  metric_name = "Invocations"
  statistic   = "Sum"
  period      = 86400
  dimensions  = { FunctionName = aws_lambda_function.scheduled_jobs.function_name }
}

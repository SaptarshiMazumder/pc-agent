# ─────────────────────────────────────────────────────────────────────────────
# ECS HOST WATCHDOG — replaces a host whose ECS agent has been disconnected too long.
#
# The failure it exists for (production, 2026-09-26): the daemon's host ran out of memory as a
# whole, thrashed, and its ECS agent disconnected. With no agent, ECS could neither finish
# stopping the frozen task nor start a new one, and the daemon stayed down until a human
# rebooted the box. The memory headroom in variables.tf makes that rare; this makes it
# self-healing. What it does and why is in lambda/ecs_host_watchdog.py.
#
# SEPARATE FROM scheduler.tf ON PURPOSE: that function is a dumb clock-to-HTTP relay with no
# AWS permissions of its own. This one terminates instances, so it gets its own role, scoped
# by tag to this environment's hosts only.
#
# COST: one invocation every 5 minutes (~8.6k/month) at 128 MB for well under a second — inside
# the Lambda free tier; the schedule is free.
# ─────────────────────────────────────────────────────────────────────────────

data "archive_file" "ecs_host_watchdog" {
  type        = "zip"
  source_file = "${path.module}/lambda/ecs_host_watchdog.py"
  output_path = "${path.module}/.terraform/ecs_host_watchdog.zip"
}

resource "aws_cloudwatch_log_group" "ecs_host_watchdog" {
  name              = "/aws/lambda/${local.name_prefix}-ecs-host-watchdog"
  retention_in_days = 14
  tags              = local.common_tags
}

resource "aws_iam_role" "ecs_host_watchdog" {
  name = "${local.name_prefix}-ecs-host-watchdog"
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

resource "aws_iam_role_policy_attachment" "ecs_host_watchdog_logs" {
  role       = aws_iam_role.ecs_host_watchdog.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "ecs_host_watchdog" {
  name = "watch-and-replace-hosts"
  role = aws_iam_role.ecs_host_watchdog.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ListTheCluster"
        Effect   = "Allow"
        Action   = ["ecs:ListContainerInstances"]
        Resource = aws_ecs_cluster.main.arn
      },
      {
        Sid       = "DescribeItsHosts"
        Effect    = "Allow"
        Action    = ["ecs:DescribeContainerInstances"]
        Resource  = "*"
        Condition = { ArnEquals = { "ecs:cluster" = aws_ecs_cluster.main.arn } }
      },
      {
        # DescribeTags supports no resource scoping.
        Sid      = "ReadTags"
        Effect   = "Allow"
        Action   = ["ec2:DescribeTags"]
        Resource = "*"
      },
      {
        # Only this environment's own hosts, by the tags every ECS instance is launched with.
        Sid      = "TagAndReplaceOwnHosts"
        Effect   = "Allow"
        Action   = ["ec2:CreateTags", "ec2:DeleteTags", "ec2:TerminateInstances"]
        Resource = "arn:aws:ec2:*:${data.aws_caller_identity.current.account_id}:instance/*"
        Condition = {
          StringEquals = {
            "aws:ResourceTag/Project"     = var.project
            "aws:ResourceTag/Environment" = var.environment
          }
        }
      },
      {
        Sid      = "Announce"
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = aws_sns_topic.alerts.arn
      },
    ]
  })
}

resource "aws_lambda_function" "ecs_host_watchdog" {
  function_name    = "${local.name_prefix}-ecs-host-watchdog"
  role             = aws_iam_role.ecs_host_watchdog.arn
  runtime          = "python3.12"
  handler          = "ecs_host_watchdog.handler"
  filename         = data.archive_file.ecs_host_watchdog.output_path
  source_code_hash = data.archive_file.ecs_host_watchdog.output_base64sha256
  timeout          = 60
  memory_size      = 128
  description      = "Terminates an ECS host whose agent has been disconnected for 10+ minutes, so its group replaces it."

  environment {
    variables = {
      CLUSTER   = aws_ecs_cluster.main.name
      TOPIC_ARN = aws_sns_topic.alerts.arn
      # Long enough that a deliberate reboot (agent back in ~2-3 min) never trips it.
      GRACE_SECONDS = "600"
    }
  }

  depends_on = [aws_cloudwatch_log_group.ecs_host_watchdog]
  tags       = local.common_tags
}

resource "aws_iam_role_policy" "scheduler_invoke_watchdog" {
  name = "invoke-ecs-host-watchdog"
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["lambda:InvokeFunction"]
      Resource = [aws_lambda_function.ecs_host_watchdog.arn]
    }]
  })
}

resource "aws_scheduler_schedule" "ecs_host_watchdog" {
  name        = "${local.name_prefix}-ecs-host-watchdog"
  description = "Replace ECS hosts whose agent has been disconnected for 10+ minutes."
  # Off while paused: there are no hosts to watch, and nothing should be terminating anything.
  state = local.paused ? "DISABLED" : "ENABLED"

  schedule_expression = "rate(5 minutes)"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.ecs_host_watchdog.arn
    role_arn = aws_iam_role.scheduler.arn
    # A missed tick is simply picked up by the next one; no retry is needed.
    retry_policy {
      maximum_retry_attempts = 0
    }
  }
}

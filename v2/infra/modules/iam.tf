# The two roles every ECS task wears.
#   • execution role = what AWS needs to START a task: pull the image, write logs, read secrets.
#   • task role      = what your CODE may do at runtime: mount EFS.

# Account id + region are needed to build the secrets ARN below.
data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# Trust policy: "the ECS tasks service is allowed to wear these roles."
data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# ── Execution role ──
resource "aws_iam_role" "execution" {
  name               = "${local.name_prefix}-ecs-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
  tags               = local.common_tags
}

# AWS-managed policy granting exactly "pull from ECR + write to CloudWatch Logs".
resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Allow reading ONLY this environment's secrets (agentd/dev/* — never prod's).
resource "aws_iam_role_policy" "execution_secrets" {
  name = "read-agentd-secrets"
  role = aws_iam_role.execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = "arn:aws:secretsmanager:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:secret:${var.project}/${var.environment}/*"
    }]
  })
}

# ── Task roles ──
#
# TWO, NOT ONE, and the split is the whole point. Every service used to wear a single shared task
# role, which was fine while that role could only mount EFS. The admin control plane needs to read
# key METADATA, write a provider key and roll a service — and granting that to one shared role
# would hand the same power to the daemon, the web container and the model proxy, none of which
# have any use for it. A container that runs third-party agent code must not be able to read the
# secret it would need to impersonate the platform.
#
# So: `task` is what every service wears, `task_admin` is what a service wearing `admin_plane =
# true` wears instead (today, accounts alone). Both get the baseline below; only the second gets
# the control-plane grants.
locals {
  task_roles = {
    base  = aws_iam_role.task.id
    admin = aws_iam_role.task_admin.id
  }
}

resource "aws_iam_role" "task" {
  name               = "${local.name_prefix}-ecs-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
  tags               = local.common_tags
}

resource "aws_iam_role" "task_admin" {
  name               = "${local.name_prefix}-ecs-task-admin"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
  tags               = local.common_tags
}

# Allow the containers to mount + write EFS (per-user files, the accounts database).
resource "aws_iam_role_policy" "task_efs" {
  for_each = local.task_roles
  name     = "efs-access"
  role     = each.value
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "elasticfilesystem:ClientMount",
        "elasticfilesystem:ClientWrite",
      ]
      Resource = "*"
    }]
  })
}

# ECS exec (var.enable_execute_command): the SSM agent inside the task opens its control/data
# channels with the TASK role. Gated on the same knob, so the permission only exists where the
# door is meant to open at all.
resource "aws_iam_role_policy" "task_ecs_exec" {
  for_each = var.enable_execute_command ? local.task_roles : {}
  name     = "ecs-exec-ssm"
  role     = each.value
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "ssmmessages:CreateControlChannel",
        "ssmmessages:CreateDataChannel",
        "ssmmessages:OpenControlChannel",
        "ssmmessages:OpenDataChannel",
      ]
      Resource = "*"
    }]
  })
}

# ── The control plane's own grants ──
#
# STATED PLAINLY, because it is a real trade and not a formality: this makes the accounts task the
# most valuable target in the deployment. It can read every platform secret and start a rollout.
# That is the price of setting a provider key from a browser instead of the CLI, and it is bounded
# three ways — a separate role so no other container inherits it, ARNs scoped to THIS environment's
# resources, and no delete/create verbs anywhere in it.
resource "aws_iam_role_policy" "task_admin_plane" {
  name = "admin-control-plane"
  role = aws_iam_role.task_admin.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Read to render the keys panel (names + rotation dates only), write to set one.
        # Scoped to this environment's secrets: dev must never be able to touch production's.
        Sid    = "PlatformSecrets"
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue",
          "secretsmanager:PutSecretValue",
          "secretsmanager:DescribeSecret",
        ]
        Resource = "arn:aws:secretsmanager:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:secret:${var.project}/${var.environment}/*"
      },
      {
        # The creator + root key inventory. SCAN ONLY — the dashboard reports which keys exist and
        # whether they are KMS-wrapped; changing a creator's state goes through the publish
        # service, which owns that table and the ordering rules that keep the roster consistent.
        Sid      = "CreatorKeyInventory"
        Effect   = "Allow"
        Action   = ["dynamodb:Scan", "dynamodb:DescribeTable"]
        Resource = aws_dynamodb_table.creators.arn
      },
      {
        # A secret write does nothing until the containers that read it restart, so rolling them
        # is part of the action rather than a follow-up an operator has to remember. UpdateService
        # only: no scaling verbs, no create, no delete.
        Sid      = "RollServicesAfterASecretChange"
        Effect   = "Allow"
        Action   = ["ecs:UpdateService", "ecs:DescribeServices"]
        Resource = "arn:aws:ecs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:service/${aws_ecs_cluster.main.name}/*"
      },
    ]
  })
}

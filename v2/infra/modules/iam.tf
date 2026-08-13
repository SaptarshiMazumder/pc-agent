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

# ── Task role ──
resource "aws_iam_role" "task" {
  name               = "${local.name_prefix}-ecs-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
  tags               = local.common_tags
}

# Allow the daemon container to mount + write EFS (per-user files).
resource "aws_iam_role_policy" "task_efs" {
  name = "efs-access"
  role = aws_iam_role.task.id
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
  count = var.enable_execute_command ? 1 : 0
  name  = "ecs-exec-ssm"
  role  = aws_iam_role.task.id
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

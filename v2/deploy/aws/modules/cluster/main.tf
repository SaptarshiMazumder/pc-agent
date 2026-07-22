# modules/cluster — the runtime: an ECS cluster (where Fargate tasks run), a log group for
# their output, and a private DNS namespace so containers can reach each other by name.

locals {
  name_prefix = "${var.project}-${var.environment}"
  common_tags = {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

# The cluster is just a grouping; it costs nothing empty (you pay per running task).
resource "aws_ecs_cluster" "main" {
  name = local.name_prefix
  tags = local.common_tags
}

# Where each container streams its stdout/stderr. Auto-deleted after 14 days (cost control).
resource "aws_cloudwatch_log_group" "agentd" {
  name              = "/${var.project}/${var.environment}"
  retention_in_days = 14
  tags              = local.common_tags
}

# Private DNS so `daemon` can reach gateway:4000 / accounts:4100 by name (as compose does).
# Each environment has its own VPC, so the same name is safe across environments.
resource "aws_service_discovery_private_dns_namespace" "main" {
  name        = "${var.project}.local"
  description = "Internal service discovery for ${local.name_prefix}"
  vpc         = var.vpc_id
  tags        = local.common_tags
}

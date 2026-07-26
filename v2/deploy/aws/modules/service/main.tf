# modules/service — everything needed to RUN one container on Fargate:
#   • a task definition (the blueprint: image, cpu/mem, env, secrets, logs, optional EFS)
#   • a service-discovery entry (so siblings reach it at <name>.<namespace>)
#   • an ECS service (keeps N copies alive; optionally registered with an ALB target group)
# The 4 containers are just this module, called 4 times with different inputs.

locals {
  name_prefix = "${var.project}-${var.environment}"
  full_name   = "${local.name_prefix}-${var.name}"
  common_tags = {
    Project     = var.project
    Environment = var.environment
    Component   = var.name
    ManagedBy   = "terraform"
  }

  mount_efs    = var.efs_access_point_id != ""
  mount_points = local.mount_efs ? [{ sourceVolume = "data", containerPath = var.mount_path, readOnly = false }] : []
}

# ── The blueprint ──
resource "aws_ecs_task_definition" "this" {
  family                   = local.full_name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.task_role_arn

  # Only attach an EFS volume when an access point was supplied.
  dynamic "volume" {
    for_each = local.mount_efs ? [1] : []
    content {
      name = "data"
      efs_volume_configuration {
        file_system_id     = var.efs_id
        transit_encryption = "ENABLED"
        authorization_config {
          access_point_id = var.efs_access_point_id
          iam             = "ENABLED"
        }
      }
    }
  }

  container_definitions = jsonencode([{
    name         = var.name
    image        = var.image
    essential    = true
    portMappings = [{ containerPort = var.container_port }]
    environment  = [for k, v in var.environment_vars : { name = k, value = v }]
    secrets      = [for k, v in var.secrets : { name = k, valueFrom = v }]
    mountPoints  = local.mount_points
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = var.log_group_name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = var.name
      }
    }
  }])

  tags = local.common_tags
}

# ── Service discovery: makes this reachable at <name>.<namespace> (e.g. gateway.agentd.local) ──
resource "aws_service_discovery_service" "this" {
  name = var.name

  dns_config {
    namespace_id = var.namespace_id
    dns_records {
      ttl  = 10
      type = "A"
    }
    routing_policy = "MULTIVALUE"
  }

  health_check_custom_config {
    failure_threshold = 1
  }

  tags = local.common_tags
}

# ── The service: keeps desired_count copies alive, wired to the network + (optionally) the ALB ──
resource "aws_ecs_service" "this" {
  name            = local.full_name
  cluster         = var.cluster_arn
  task_definition = aws_ecs_task_definition.this.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [var.security_group_id]
    assign_public_ip = true # public subnets, no NAT → tasks need a public IP to reach ECR/internet
  }

  service_registries {
    registry_arn = aws_service_discovery_service.this.arn
  }

  # Attach to the ALB only for internet-exposed services (web/accounts/daemon).
  dynamic "load_balancer" {
    for_each = var.exposed ? [1] : []
    content {
      target_group_arn = var.target_group_arn
      container_name   = var.name
      container_port   = var.container_port
    }
  }

  # Terraform sets the INITIAL count, then stops managing it — so down.ps1/up.ps1 can scale
  # tasks to 0 (pause the compute bill) and back to 1 without `apply` reverting them.
  lifecycle {
    ignore_changes = [desired_count]
  }

  tags = local.common_tags
}

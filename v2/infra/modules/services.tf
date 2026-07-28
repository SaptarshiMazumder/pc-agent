# The containers. ONE set of for_each resources over the services map — per service:
#   • a task definition (the blueprint: image, cpu/mem, env, secrets, logs, optional EFS)
#   • a service-discovery entry (so siblings reach it at <name>.agentd.local)
#   • an ECS service (keeps N copies alive, registered with its ALB target group)
# Adding a container to the platform = adding an entry to local.services. That's it.

resource "aws_ecs_task_definition" "svc" {
  for_each = local.services

  family                   = "${local.name_prefix}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = each.value.cpu
  memory                   = each.value.memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  # Only attach an EFS volume when the service asks for one (efs = true).
  dynamic "volume" {
    for_each = each.value.efs ? [1] : []
    content {
      name = "data"
      efs_volume_configuration {
        file_system_id     = aws_efs_file_system.main.id
        transit_encryption = "ENABLED"
        authorization_config {
          access_point_id = aws_efs_access_point.data.id
          iam             = "ENABLED"
        }
      }
    }
  }

  container_definitions = jsonencode([{
    name         = each.key
    image        = "${aws_ecr_repository.this[each.key].repository_url}:${var.image_tag}"
    essential    = true
    portMappings = [{ containerPort = each.value.port }]
    environment  = [for k, v in each.value.env : { name = k, value = v }]
    # secret_keys: container env var -> JSON key inside the app secret.
    secrets = [for k, v in each.value.secret_keys : {
      name      = k
      valueFrom = "${aws_secretsmanager_secret.app.arn}:${v}::"
    }]
    mountPoints = each.value.efs ? [{ sourceVolume = "data", containerPath = "/data", readOnly = false }] : []
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.agentd.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = each.key
      }
    }
  }])

  tags = merge(local.common_tags, { Component = each.key })
}

# Service discovery: makes each service reachable at <name>.agentd.local.
resource "aws_service_discovery_service" "svc" {
  for_each = local.services

  name = each.key

  dns_config {
    namespace_id = aws_service_discovery_private_dns_namespace.main.id
    dns_records {
      ttl  = 10
      type = "A"
    }
    routing_policy = "MULTIVALUE"
  }

  health_check_custom_config {
    failure_threshold = 1
  }

  lifecycle {
    create_before_destroy = true
  }

  tags = merge(local.common_tags, { Component = each.key })
}

# The service: keeps desired_count copies alive, wired to the network + the ALB.
resource "aws_ecs_service" "svc" {
  for_each = local.services

  name            = "${local.name_prefix}-${each.key}"
  cluster         = aws_ecs_cluster.main.arn
  task_definition = aws_ecs_task_definition.svc[each.key].arn
  desired_count   = each.value.desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.service.id]
    assign_public_ip = true # public subnets, no NAT → tasks need a public IP to reach ECR/internet
  }

  service_registries {
    registry_arn = aws_service_discovery_service.svc[each.key].arn
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.svc[each.key].arn
    container_name   = each.key
    container_port   = each.value.port
  }

  # Terraform sets the INITIAL count, then stops managing it — so down.ps1/up.ps1 can scale
  # tasks to 0 (pause the compute bill) and back to 1 without `apply` reverting them.
  lifecycle {
    create_before_destroy = true
    ignore_changes        = [desired_count]
  }

  tags = merge(local.common_tags, { Component = each.key })
}

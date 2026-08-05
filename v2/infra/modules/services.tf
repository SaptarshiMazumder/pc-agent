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
  # Removed while HIBERNATING, not merely scaled to zero. The service's load-balancer attachment
  # would otherwise have to be edited in place when the ALB comes and goes, and the provider may
  # treat that as a replacement — which an ECS service cannot survive under create_before_destroy,
  # because two services in one cluster may not share a name. Deleting and recreating a stateless
  # service is the boring option, and boring is what a cost switch should be.
  for_each = local.alb_services

  name            = "${local.name_prefix}-${each.key}"
  cluster         = aws_ecs_cluster.main.arn
  task_definition = aws_ecs_task_definition.svc[each.key].arn
  launch_type     = "FARGATE"

  # THE COST SWITCH (var.paused). Terraform owns this number so that turning the environment off
  # and on is `terraform apply` — the command you already run — and so the set of services being
  # paused is derived from local.services rather than typed into a script that has to be
  # remembered every time a service is added. A hand-maintained list had already drifted: it
  # still named four services after `ingest` became the fifth, so a "down" left one running.
  desired_count = local.paused ? 0 : each.value.desired_count

  # Without this, ECS retries a task that cannot start FOREVER: a container that crashes on
  # boot is relaunched indefinitely, the deployment never reaches a terminal state, and the
  # only thing that eventually gives up is the deploy workflow's waiter — after ten minutes,
  # with "Max attempts exceeded" and no hint of the cause. The breaker makes a broken image
  # fail as a DEPLOYMENT failure (rolloutState FAILED, with the reason) and puts the previous
  # working task definition back, which is what "the deploy failed" should mean.
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  # Boot grace: health-check failures in this window don't count against the task, so a slow
  # starter is not killed before it can serve. It does NOT rescue a crash loop — a container
  # that exits is still a failure — so it costs the breaker nothing.
  health_check_grace_period_seconds = each.value.health_check_grace

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

  # `ignore_changes = [desired_count]` USED TO BE HERE, so that down.ps1/up.ps1 could scale tasks
  # outside Terraform. It is gone on purpose: two mechanisms owned the same number, so the state
  # file and reality disagreed by design, and the only way to know whether an environment was
  # paused was to ask AWS. Terraform owns it now (var.paused) — which is also what makes pausing
  # a plain `apply` instead of a separate script.
  lifecycle {
    create_before_destroy = true
  }

  tags = merge(local.common_tags, { Component = each.key })
}

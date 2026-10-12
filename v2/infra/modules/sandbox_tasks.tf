# Untrusted work gets one fresh Fargate task. Never mount EFS or attach a task role.
locals {
  sandbox_images = merge(
    local.executor_enabled ? { executor = "${aws_ecr_repository.executor.repository_url}:${var.executor_image_tag}" } : {},
    local.builder_enabled ? { builder = "${aws_ecr_repository.builder.repository_url}:${var.builder_image_tag}" } : {}
  )
  sandbox_env = {
    for kind, definition in aws_ecs_task_definition.sandbox : kind => {
      SANDBOX_CLUSTER         = aws_ecs_cluster.main.arn
      SANDBOX_TASK_DEFINITION = definition.arn
      SANDBOX_SUBNETS         = join(",", aws_subnet.public[*].id)
      SANDBOX_SECURITY_GROUP  = aws_security_group.sandbox.id
      SANDBOX_LEASE_TABLE     = aws_dynamodb_table.sandbox_leases.name
    }
  }
}

resource "aws_security_group" "sandbox" {
  name   = "${local.name_prefix}-sandbox"
  vpc_id = aws_vpc.main.id
  tags   = local.common_tags
}

resource "aws_security_group" "sandbox_endpoints" {
  name   = "${local.name_prefix}-sandbox-endpoints"
  vpc_id = aws_vpc.main.id
  ingress {
    protocol    = "tcp"
    from_port   = 443
    to_port     = 443
    cidr_blocks = [aws_vpc.main.cidr_block]
  }
  tags = local.common_tags
}

resource "aws_vpc_endpoint" "sandbox_s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${data.aws_region.current.name}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.public.id]
  tags              = local.common_tags
}

resource "aws_vpc_endpoint" "sandbox_bootstrap" {
  for_each            = toset(["ecr.api", "ecr.dkr", "logs"])
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${data.aws_region.current.name}.${each.key}"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true
  subnet_ids          = aws_subnet.public[*].id
  security_group_ids  = [aws_security_group.sandbox_endpoints.id]
  tags                = local.common_tags
}

resource "aws_vpc_security_group_egress_rule" "sandbox_s3" {
  security_group_id = aws_security_group.sandbox.id
  prefix_list_id    = aws_vpc_endpoint.sandbox_s3.prefix_list_id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_security_group_egress_rule" "sandbox_bootstrap" {
  security_group_id            = aws_security_group.sandbox.id
  referenced_security_group_id = aws_security_group.sandbox_endpoints.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
}

resource "aws_security_group" "sandbox_proxy" {
  name   = "${local.name_prefix}-sandbox-proxy"
  vpc_id = aws_vpc.main.id
  egress {
    protocol    = "tcp"
    from_port   = 80
    to_port     = 80
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    protocol    = "tcp"
    from_port   = 443
    to_port     = 443
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = local.common_tags
}

resource "aws_vpc_security_group_egress_rule" "sandbox_proxy" {
  security_group_id            = aws_security_group.sandbox.id
  referenced_security_group_id = aws_security_group.sandbox_proxy.id
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
}

resource "aws_vpc_security_group_ingress_rule" "sandbox_proxy" {
  security_group_id            = aws_security_group.sandbox_proxy.id
  referenced_security_group_id = aws_security_group.sandbox.id
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
}

resource "aws_iam_role" "sandbox_execution" {
  name = "${local.name_prefix}-sandbox-execution"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Condition = { StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id } }
    }]
  })
  tags = local.common_tags
}

resource "aws_iam_role_policy" "sandbox_execution" {
  role = aws_iam_role.sandbox_execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
      {
        Effect   = "Allow"
        Action   = ["ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage"]
        Resource = [aws_ecr_repository.executor.arn, aws_ecr_repository.builder.arn]
      },
      {
        Effect   = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.agentd.arn}:*"
      }
    ]
  })
}

resource "aws_ecs_task_definition" "sandbox" {
  for_each                 = local.sandbox_images
  family                   = "${local.name_prefix}-sandbox-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "1024"
  memory                   = "2048"
  execution_role_arn       = aws_iam_role.sandbox_execution.arn
  # Intentionally no task_role_arn. Execution role is never available inside the container.
  volume {
    name                = "scratch"
    configure_at_launch = false
  }
  container_definitions = jsonencode([{
    name                   = "sandbox", image = each.value, essential = true, user = "1000"
    entryPoint             = ["python3", "/var/task/fargate_runner.py"]
    command                = []
    readonlyRootFilesystem = true
    dependsOn              = [{ containerName = "scratch-init", condition = "SUCCESS" }]
    linuxParameters        = { capabilities = { add = [], drop = ["ALL"] } }
    portMappings           = []
    systemControls         = []
    volumesFrom            = []
    mountPoints            = [{ sourceVolume = "scratch", containerPath = "/tmp", readOnly = false }]
    environment = [
      { name = "HOME", value = "/tmp" },
      { name = "AGENTD_HOME", value = "/tmp/agentd" },
      { name = "XDG_CACHE_HOME", value = "/tmp/cache" },
      { name = "UV_CACHE_DIR", value = "/tmp/uv/cache" },
      { name = "UV_TOOL_DIR", value = "/tmp/uv/tools" },
      { name = "UV_TOOL_BIN_DIR", value = "/tmp/uv/bin" },
      { name = "AGENTD_ISOLATED_TASK", value = "1" },
      { name = "HTTP_PROXY", value = "http://sandbox-egress.${var.project}.local:8080" },
      { name = "HTTPS_PROXY", value = "http://sandbox-egress.${var.project}.local:8080" },
      { name = "http_proxy", value = "http://sandbox-egress.${var.project}.local:8080" },
      { name = "https_proxy", value = "http://sandbox-egress.${var.project}.local:8080" },
      { name = "NO_PROXY", value = "${aws_s3_bucket.builder_scratch.bucket}.s3.${data.aws_region.current.name}.amazonaws.com" }
    ]
    logConfiguration = { logDriver = "awslogs", options = {
      awslogs-group  = aws_cloudwatch_log_group.agentd.name
      awslogs-region = data.aws_region.current.name, awslogs-stream-prefix = "sandbox-${each.key}"
    } }
    }, {
    # Fargate empty volumes start root-owned with mode 0755. A trusted helper
    # prepares only this task's disposable disk, exits, then the unprivileged
    # executor starts. It has no role, secrets, tenant mounts or added capabilities.
    name                   = "scratch-init", image = each.value, essential = false, user = "0"
    entryPoint             = ["python3", "-c"]
    command                = ["import os; os.chmod('/tmp', 0o1777)"]
    readonlyRootFilesystem = true
    linuxParameters        = { capabilities = { add = [], drop = ["ALL"] } }
    portMappings           = []
    systemControls         = []
    volumesFrom            = []
    environment            = []
    mountPoints            = [{ sourceVolume = "scratch", containerPath = "/tmp", readOnly = false }]
    logConfiguration = { logDriver = "awslogs", options = {
      awslogs-group  = aws_cloudwatch_log_group.agentd.name
      awslogs-region = data.aws_region.current.name, awslogs-stream-prefix = "sandbox-init"
    } }
  }])
  tags = local.common_tags
}

resource "aws_service_discovery_service" "sandbox_proxy" {
  name = "sandbox-egress"
  dns_config {
    namespace_id = aws_service_discovery_private_dns_namespace.main.id
    dns_records {
      type = "A"
      ttl  = 10
    }
    routing_policy = "MULTIVALUE"
  }
  health_check_custom_config { failure_threshold = 1 }
}

resource "aws_ecs_task_definition" "sandbox_proxy" {
  count                    = local.executor_enabled ? 1 : 0
  family                   = "${local.name_prefix}-sandbox-egress"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.sandbox_execution.arn
  container_definitions = jsonencode([{
    name                   = "proxy", image = local.sandbox_images.executor, essential = true
    entryPoint             = ["python3", "/var/task/egress_proxy.py"], command = [], user = "1000"
    readonlyRootFilesystem = true
    linuxParameters        = { capabilities = { add = [], drop = ["ALL"] } }
    portMappings           = [{ containerPort = 8080, hostPort = 8080, protocol = "tcp" }]
    mountPoints            = []
    systemControls         = []
    volumesFrom            = []
    environment            = []
    logConfiguration = { logDriver = "awslogs", options = {
      awslogs-group  = aws_cloudwatch_log_group.agentd.name
      awslogs-region = data.aws_region.current.name, awslogs-stream-prefix = "sandbox-egress"
    } }
  }])
  tags = local.common_tags
}

resource "aws_ecs_service" "sandbox_proxy" {
  count           = local.executor_enabled ? 1 : 0
  name            = "${local.name_prefix}-sandbox-egress"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.sandbox_proxy[0].arn
  desired_count   = local.paused ? 0 : 1
  launch_type     = "FARGATE"
  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.sandbox_proxy.id]
    assign_public_ip = true
  }
  service_registries { registry_arn = aws_service_discovery_service.sandbox_proxy.arn }
  depends_on = [aws_vpc_endpoint.sandbox_bootstrap, aws_iam_role_policy.sandbox_execution]
  tags       = local.common_tags
}

resource "aws_iam_role_policy" "sandbox_controller" {
  for_each = { executor = aws_iam_role.executor.id, builder = aws_iam_role.builder.id }
  role     = each.value
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow", Action = ["ecs:RunTask"]
        Resource  = "arn:aws:ecs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:task-definition/${local.name_prefix}-sandbox-${each.key}:*"
        Condition = { ArnEquals = { "ecs:cluster" = aws_ecs_cluster.main.arn } }
      },
      {
        Effect   = "Allow", Action = ["ecs:StopTask", "ecs:DescribeTasks", "ecs:TagResource"]
        Resource = "arn:aws:ecs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:task/${aws_ecs_cluster.main.name}/*"
      },
      {
        Effect    = "Allow", Action = ["iam:PassRole"], Resource = aws_iam_role.sandbox_execution.arn
        Condition = { StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" } }
      }
      , {
        Effect   = "Allow", Action = ["dynamodb:PutItem", "dynamodb:DeleteItem"]
        Resource = aws_dynamodb_table.sandbox_leases.arn
      }
    ]
  })
}

resource "random_password" "state_kek" {
  length  = 64
  special = false
}

resource "aws_dynamodb_table" "sandbox_leases" {
  name         = "${local.name_prefix}-runtime-leases"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  attribute {
    name = "pk"
    type = "S"
  }
  ttl {
    attribute_name = "expires"
    enabled        = true
  }
  tags = local.common_tags
}

data "archive_file" "sandbox_reaper" {
  type        = "zip"
  output_path = "${path.module}/.terraform/sandbox_reaper.zip"
  source {
    filename = "sandbox_reaper.py"
    content  = file("${path.module}/lambda/sandbox_reaper.py")
  }
  source {
    filename = "runtime_leases.py"
    content  = file("${path.module}/../../services/executor/runtime_leases.py")
  }
}

resource "aws_iam_role" "sandbox_reaper" {
  name = "${local.name_prefix}-runtime-reaper"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "lambda.amazonaws.com" }
  }] })
  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "sandbox_reaper_logs" {
  role       = aws_iam_role.sandbox_reaper.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "sandbox_reaper" {
  role = aws_iam_role.sandbox_reaper.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["ecs:ListTasks"], Resource = "*", Condition = { ArnEquals = { "ecs:cluster" = aws_ecs_cluster.main.arn } } },
    { Effect = "Allow", Action = ["ecs:DescribeTasks", "ecs:StopTask"], Resource = "arn:aws:ecs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:task/${aws_ecs_cluster.main.name}/*" },
    { Effect = "Allow", Action = ["dynamodb:DeleteItem"], Resource = aws_dynamodb_table.sandbox_leases.arn }
  ] })
}

resource "aws_cloudwatch_log_group" "sandbox_reaper" {
  name              = "/aws/lambda/${local.name_prefix}-runtime-reaper"
  retention_in_days = 14
  tags              = local.common_tags
}

resource "aws_lambda_function" "sandbox_reaper" {
  function_name                  = "${local.name_prefix}-runtime-reaper"
  role                           = aws_iam_role.sandbox_reaper.arn
  runtime                        = "python3.12"
  handler                        = "sandbox_reaper.handler"
  filename                       = data.archive_file.sandbox_reaper.output_path
  source_code_hash               = data.archive_file.sandbox_reaper.output_base64sha256
  timeout                        = 55
  memory_size                    = 128
  reserved_concurrent_executions = 1
  environment {
    variables = {
      SANDBOX_CLUSTER       = aws_ecs_cluster.main.arn
      SANDBOX_FAMILY_PREFIX = "${local.name_prefix}-sandbox-"
      SANDBOX_LEASE_TABLE   = aws_dynamodb_table.sandbox_leases.name
    }
  }
  depends_on = [aws_iam_role_policy.sandbox_reaper, aws_cloudwatch_log_group.sandbox_reaper]
  tags       = local.common_tags
}

resource "aws_cloudwatch_event_rule" "sandbox_reaper" {
  name                = "${local.name_prefix}-runtime-reaper"
  schedule_expression = "rate(1 minute)"
  state               = local.paused ? "DISABLED" : "ENABLED"
  tags                = local.common_tags
}

resource "aws_cloudwatch_event_target" "sandbox_reaper" {
  rule = aws_cloudwatch_event_rule.sandbox_reaper.name
  arn  = aws_lambda_function.sandbox_reaper.arn
}

resource "aws_lambda_permission" "sandbox_reaper" {
  statement_id  = "RuntimeDeadlineClock"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.sandbox_reaper.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.sandbox_reaper.arn
}

resource "aws_vpc_security_group_ingress_rule" "sandbox_proxy_control" {
  security_group_id            = aws_security_group.sandbox_proxy.id
  referenced_security_group_id = aws_security_group.service.id
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
}

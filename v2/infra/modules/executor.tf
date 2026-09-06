# The EXECUTOR SERVICE — untrusted code off the daemon, one microVM per call.
#
# WHY IT EXISTS. The daemon's plugin sandbox isolated untrusted tool calls in a child PROCESS on
# the daemon's own box — same kernel, same filesystem, same network as every tenant's data. The
# hosted rule is stricter: untrusted code (a marketplace plugin's tools, an authored agent's
# private tools, a builder-directed shell command) never executes on the main daemon. Each Lambda
# invocation is its own Firecracker microVM; services/executor/handler.py is what runs inside.
#
# THE PATTERN IS builder.tf, DELIBERATELY: container-image Lambda, two-step bring-up (empty
# image tag = repo only, no function), ALB lambda target on its own port (:4500), NOT in the VPC
# (no NAT, no endpoints — outside, S3 just works), terraform-minted internal key. It SHARES the
# builder's scratch bucket: both are conveyor belts with a 1-day expiry, and a second bucket
# would be a second lifecycle rule guarding identical garbage.
#
# THE IMAGE IS BUILT FROM THE DAEMON IMAGE (services/executor/Dockerfile) so the sandbox worker
# runs with byte-identical site-packages — a plugin that works on desktop cannot fail here with
# an import error nobody can reproduce. Push order therefore matters: daemon image first,
# executor built FROM it, tag set, apply.

locals {
  executor_enabled = var.executor_image_tag != ""
  executor_name    = "${local.name_prefix}-executor"

  # One expression feeds the output and the daemon's env var — same derivation as the builder's,
  # so the two can never drift apart.
  executor_public_url = (
    local.executor_enabled && local.public_host != ""
    ? "${local.url_scheme}://${local.public_host}:${var.executor_listener_port}"
    : ""
  )
}

# ─────────────────────────────── image ───────────────────────────────

resource "aws_ecr_repository" "executor" {
  name                 = "${local.name_prefix}/executor"
  image_tag_mutability = var.image_tag_mutability
  force_delete         = var.ecr_force_delete

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = merge(local.common_tags, {
    Name      = "${local.name_prefix}/executor"
    Component = "executor"
  })
}

# ─────────────────────────────── shared secret ───────────────────────────────
#
# Same trade the builder documents: terraform-minted, rides task-definition + Lambda env. What
# this key guards is "may submit sandbox jobs and answer their broker frames" — the jobs carry
# no credentials (the daemon brokers every model/fetch call itself), so the blast radius of a
# leaked key is burned compute plus whatever code the caller could already run as themselves.
resource "random_password" "executor_internal_key" {
  length  = 40
  special = false
}

# ─────────────────────────────── role ───────────────────────────────

resource "aws_iam_role" "executor" {
  name = local.executor_name

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })

  tags = merge(local.common_tags, { Component = "executor" })
}

resource "aws_iam_role_policy_attachment" "executor_logs" {
  role       = aws_iam_role.executor.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Exactly one job's needs against the shared scratch bucket: read the code/workspace zips the
# daemon uploaded, write broker request frames + the changed-files zip, read the daemon's broker
# answers, and HEAD the code cache. No delete (the lifecycle rule cleans), no other bucket.
resource "aws_iam_role_policy" "executor" {
  name = "${local.executor_name}-access"
  role = aws_iam_role.executor.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:GetObject", "s3:PutObject"]
      Resource = "${aws_s3_bucket.builder_scratch.arn}/*"
    }]
  })
}

# (The daemon's own Get/Put on the scratch bucket already exists — builder.tf's
# task_builder_scratch policy on the shared task role — and presigned URLs carry the LAMBDA's
# authority anyway, so no new task-role grant is needed here.)

# ─────────────────────────────── function ───────────────────────────────

resource "aws_lambda_function" "executor" {
  count = local.executor_enabled ? 1 : 0

  function_name = local.executor_name
  role          = aws_iam_role.executor.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.executor.repository_url}:${var.executor_image_tag}"
  description   = "POST /executor - runs one untrusted sandbox job (tool call / enumerate / shell) in this invocation's microVM."

  timeout     = var.executor_timeout_seconds
  memory_size = var.executor_memory_mb

  # Code + workspace copies + the tool's own writes all live in /tmp for the invocation.
  ephemeral_storage {
    size = 4096
  }

  # DELIBERATELY NOT IN THE VPC — same constraint builder.tf documents: this VPC has no NAT and
  # no endpoints, so an in-VPC function has no route to S3, which is every byte of this design's
  # transport. Outside, S3 and the broker slots just work. The untrusted code inside still runs
  # under the sandbox worker's guard (no net except brokered, no env, no host paths).

  environment {
    variables = {
      EXECUTOR_SCRATCH_BUCKET = aws_s3_bucket.builder_scratch.bucket
      EXECUTOR_INTERNAL_KEY   = random_password.executor_internal_key.result
      LOG_LEVEL               = "INFO"
    }
  }

  tags = merge(local.common_tags, { Name = local.executor_name, Component = "executor" })
}

# ─────────────────────────────── route ───────────────────────────────

resource "aws_vpc_security_group_ingress_rule" "alb_executor" {
  count = local.executor_enabled ? 1 : 0

  security_group_id = aws_security_group.alb.id
  description       = "executor service from anywhere"
  ip_protocol       = "tcp"
  from_port         = var.executor_listener_port
  to_port           = var.executor_listener_port
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_lb_target_group" "executor" {
  count = local.executor_enabled ? 1 : 0

  name        = substr("${local.executor_name}-tg", 0, 32)
  target_type = "lambda"

  lambda_multi_value_headers_enabled = false

  tags = merge(local.common_tags, { Name = local.executor_name, Component = "executor" })
}

resource "aws_lambda_permission" "executor_alb" {
  count = local.executor_enabled ? 1 : 0

  statement_id  = "AllowExecutionFromALB"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.executor[0].function_name
  principal     = "elasticloadbalancing.amazonaws.com"
  source_arn    = aws_lb_target_group.executor[0].arn
}

resource "aws_lb_target_group_attachment" "executor" {
  count = local.executor_enabled ? 1 : 0

  target_group_arn = aws_lb_target_group.executor[0].arn
  target_id        = aws_lambda_function.executor[0].arn
  depends_on       = [aws_lambda_permission.executor_alb]
}

resource "aws_lb_listener" "executor" {
  count = local.executor_enabled && length(aws_lb.main) > 0 ? 1 : 0

  load_balancer_arn = aws_lb.main[0].arn
  port              = var.executor_listener_port
  protocol          = local.tls_enabled ? "HTTPS" : "HTTP"
  ssl_policy        = local.tls_enabled ? "ELBSecurityPolicy-TLS13-1-2-2021-06" : null
  certificate_arn   = local.tls_enabled ? local.listener_certificate_arn : null

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.executor[0].arn
  }
}

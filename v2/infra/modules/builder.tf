# The BUILDER SERVICE — agent window builds, out of the daemon's container.
#
# WHY IT EXISTS. Creating or editing an agent's window runs `npm` + `vite`, and on the hosted
# daemon that build ran in-process — where a node build's memory peak exceeds the whole task, so
# the kernel OOM-killed the daemon mid-create and dropped every user's socket with it (exit 137,
# 2026-08-29, staging). Builds now run here: one Lambda invocation per build, sized for exactly
# one build, keeping nothing (services/builder/handler.py tells the whole story).
#
# WHY A LAMBDA AND NOT AN ECS SERVICE. Builds are bursty and idle almost all the time — a warm
# always-on builder is a bill for nothing, and Lambda's warm instances already give a build
# session container reuse. Same reasoning as publish next door, whose pattern this file mirrors
# deliberately: two-step image bring-up, ALB lambda target on its own port, NOT in the VPC.
#
# TRANSPORT IS S3, NOT BODIES. The ALB caps Lambda request/response bodies at 1 MB and a built
# ui/ does not reliably fit, so the daemon puts sources into the scratch bucket and gets a result
# key back. The scratch bucket expires everything after a day — it is a conveyor belt, not
# storage; the agent's real files live on EFS with the daemon, before and after.

locals {
  builder_enabled = var.builder_image_tag != ""
  builder_name    = "${local.name_prefix}-builder"

  # The builder's public address, same derivation as publish_public_url: one expression feeds
  # both the output and the daemon's env var, so the two can never drift apart.
  builder_public_url = (
    local.builder_enabled && local.public_host != ""
    ? "${local.url_scheme}://${local.public_host}:${var.builder_listener_port}"
    : ""
  )
}

# ─────────────────────────────── image ───────────────────────────────

resource "aws_ecr_repository" "builder" {
  name                 = "${local.name_prefix}/builder"
  image_tag_mutability = var.image_tag_mutability
  force_delete         = var.ecr_force_delete

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = merge(local.common_tags, {
    Name      = "${local.name_prefix}/builder"
    Component = "builder"
  })
}

# ─────────────────────────────── scratch bucket ───────────────────────────────

resource "aws_s3_bucket" "builder_scratch" {
  bucket        = "${local.builder_name}-scratch-${random_id.suffix.hex}"
  force_destroy = true # a conveyor belt: nothing in here is ever the only copy of anything

  tags = merge(local.common_tags, { Name = "${local.builder_name}-scratch", Component = "builder" })
}

resource "aws_s3_bucket_public_access_block" "builder_scratch" {
  bucket                  = aws_s3_bucket.builder_scratch.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Everything a build leaves behind — sources zips, result zips, logs — is garbage within the
# hour; the daemon downloads the result immediately. One day is generous debugging headroom.
resource "aws_s3_bucket_lifecycle_configuration" "builder_scratch" {
  bucket = aws_s3_bucket.builder_scratch.id

  rule {
    id     = "expire-everything"
    status = "Enabled"

    filter {}

    expiration {
      days = 1
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

# ─────────────────────────────── shared secret ───────────────────────────────
#
# The daemon presents this on every build request (X-Internal-Key). Terraform-minted rather than
# a row in the app secret bundle so bring-up needs no manual set-keys step; the trade is that it
# rides both task-definition env and Lambda env, readable by anyone with ECS/Lambda describe
# rights — acceptable for "may ask for builds", which burns compute but touches no user data.
resource "random_password" "builder_internal_key" {
  length  = 40
  special = false
}

# ─────────────────────────────── role ───────────────────────────────

resource "aws_iam_role" "builder" {
  name = local.builder_name

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })

  tags = merge(local.common_tags, { Component = "builder" })
}

resource "aws_iam_role_policy_attachment" "builder_logs" {
  role       = aws_iam_role.builder.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Exactly one build's needs: read a sources zip, write a result and a log. No delete (the
# lifecycle rule cleans), no other bucket, no tables, no keys.
resource "aws_iam_role_policy" "builder" {
  name = "${local.builder_name}-access"
  role = aws_iam_role.builder.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:GetObject", "s3:PutObject"]
      Resource = "${aws_s3_bucket.builder_scratch.arn}/*"
    }]
  })
}

# THE DAEMON'S half of the conveyor belt: put sources up, take results down. On the shared task
# role because the daemon is the only ECS service that builds; scoped to the scratch bucket so
# a daemon bug cannot touch the registry or marketplace buckets through this grant.
resource "aws_iam_role_policy" "task_builder_scratch" {
  name = "${local.builder_name}-daemon-scratch"
  role = aws_iam_role.task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:PutObject", "s3:GetObject"]
      Resource = "${aws_s3_bucket.builder_scratch.arn}/*"
    }]
  })
}

# ─────────────────────────────── function ───────────────────────────────

resource "aws_lambda_function" "builder" {
  count = local.builder_enabled ? 1 : 0

  function_name = local.builder_name
  role          = aws_iam_role.builder.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.builder.repository_url}:${var.builder_image_tag}"
  # ASCII only in API parameters (same note as publish: prose stays in comments).
  description = "POST /build - compiles one agent's app/ sources into its ui/, via the scratch bucket."

  timeout     = var.builder_timeout_seconds
  memory_size = var.builder_memory_mb

  # Sources + a writable copy of node_modules (the delta-install path) + built output all live
  # in /tmp; the baked read-only modules in the image do not count against this.
  ephemeral_storage {
    size = 2048
  }

  # DELIBERATELY NOT IN THE VPC — same constraint publish documents at length: this VPC has no
  # NAT and no endpoints, so an in-VPC function has no route to S3 (every request) or the npm
  # registry (the delta-install path). Outside the VPC both just work and cold starts are faster.

  environment {
    variables = {
      BUILDER_SCRATCH_BUCKET = aws_s3_bucket.builder_scratch.bucket
      BUILDER_INTERNAL_KEY   = random_password.builder_internal_key.result
      LOG_LEVEL              = "INFO"
    }
  }

  tags = merge(local.common_tags, { Name = local.builder_name, Component = "builder" })
}

# ─────────────────────────────── route ───────────────────────────────
#
# Its own listener port (:4400), matching publish at :4300 and every ECS service's port —
# a path rule on the web listener is the natural consolidation once TLS and a domain land.

resource "aws_vpc_security_group_ingress_rule" "alb_builder" {
  count = local.builder_enabled ? 1 : 0

  security_group_id = aws_security_group.alb.id
  description       = "builder service from anywhere"
  ip_protocol       = "tcp"
  from_port         = var.builder_listener_port
  to_port           = var.builder_listener_port
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_lb_target_group" "builder" {
  count = local.builder_enabled ? 1 : 0

  name        = substr("${local.builder_name}-tg", 0, 32)
  target_type = "lambda"

  lambda_multi_value_headers_enabled = false

  tags = merge(local.common_tags, { Name = local.builder_name, Component = "builder" })
}

resource "aws_lambda_permission" "builder_alb" {
  count = local.builder_enabled ? 1 : 0

  statement_id  = "AllowExecutionFromALB"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.builder[0].function_name
  principal     = "elasticloadbalancing.amazonaws.com"
  source_arn    = aws_lb_target_group.builder[0].arn
}

resource "aws_lb_target_group_attachment" "builder" {
  count = local.builder_enabled ? 1 : 0

  target_group_arn = aws_lb_target_group.builder[0].arn
  target_id        = aws_lambda_function.builder[0].arn
  depends_on       = [aws_lambda_permission.builder_alb]
}

resource "aws_lb_listener" "builder" {
  count = local.builder_enabled && length(aws_lb.main) > 0 ? 1 : 0

  load_balancer_arn = aws_lb.main[0].arn
  port              = var.builder_listener_port
  protocol          = local.tls_enabled ? "HTTPS" : "HTTP"
  ssl_policy        = local.tls_enabled ? "ELBSecurityPolicy-TLS13-1-2-2021-06" : null
  # local.listener_certificate_arn, NOT var.certificate_arn: the certificate may be the
  # module-managed one (dns.tf), in which case the variable is empty — alb.tf owns the
  # "which certificate" decision once, and every HTTPS listener reads it from there.
  certificate_arn   = local.tls_enabled ? local.listener_certificate_arn : null

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.builder[0].arn
  }
}

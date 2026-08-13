# The PUBLISH SERVICE — how an ordinary author gets an agent into the marketplace.
#
# WHAT IT REPLACES. Publishing used to require the registry's ed25519 private key and S3 write
# credentials on the publisher's own machine. That is right for a release engineer and impossible
# for the person this product is for: someone who installed the app, built an agent, and wants it
# listed. You cannot hand every author the key that signs your marketplace. So this function holds
# the keys, and an author sends a package plus the session token they already have.
#
# WHY A LAMBDA AND NOT AN ECS SERVICE. It needs S3 write and a signing key, is stateless, and is
# idle almost all the time. Putting it on the agentd daemon would give the container that EXECUTES
# other people's agent code write access to the store those agents are sold from — an app
# connection must never be one bug away from publishing.
#
# WHY A CONTAINER IMAGE. It compiles the per-agent Windows installer with `makensis`, which a
# zip-packaged Lambda cannot install. Whoever signs must compile (see services/publish/Dockerfile).
#
# TWO-STEP BRING-UP, on purpose. An image-based Lambda cannot be created before its image exists,
# so `publish_image_tag` is empty by default: the first apply creates the ECR repo, the tables and
# the key; you push an image; then you set the tag and apply again to create the function. No apply
# ever half-fails waiting for an image.

locals {
  publish_enabled = var.publish_image_tag != ""
  publish_name    = "${local.name_prefix}-publish"

  # The platform a PRODUCT built here signs into — the same one this deployment serves, composed
  # from the same `url_scheme`/`public_host` locals the desktop flavors get (see outputs.tf). One
  # expression for both, so an installer sold from this marketplace cannot end up pointed at a
  # different backend than the app that sold it.
  publish_product_accounts_url = local.public_host == "" ? "" : "${local.url_scheme}://${local.public_host}:${local.services["accounts"].port}"
  publish_product_proxy_url    = local.public_host == "" ? "" : "${local.url_scheme}://${local.public_host}:${local.services["model-proxy"].port}"

  # Where web-delivered agents run: the hosted DAEMON serves /apps/<id>/ on its own port (the
  # gateway's HTTP hook shares the WebSocket port). Stamped into index.json as `web.host` on
  # every publish, so store cards everywhere — including desktop — can render Open-in-browser.
  publish_web_host = local.public_host == "" ? "" : "${local.url_scheme}://${local.public_host}:${local.services["daemon"].port}"
}

# ─────────────────────────────── image ───────────────────────────────

resource "aws_ecr_repository" "publish" {
  name                 = "${local.name_prefix}/publish"
  image_tag_mutability = var.image_tag_mutability
  force_delete         = var.ecr_force_delete

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = merge(local.common_tags, {
    Name      = "${local.name_prefix}/publish"
    Component = "publish"
  })
}

# ─────────────────────────────── state ───────────────────────────────
#
# PAY_PER_REQUEST throughout: a publish is a handful of reads and writes, minutes apart at best.
# Provisioned capacity would be a bill for an idle table.

# One row per account that has ever tried to publish: their creator id, state
# (pending | listed | revoked), public key, and the KMS-encrypted private key.
resource "aws_dynamodb_table" "creators" {
  name         = "${local.publish_name}-creators"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "account_id"

  attribute {
    name = "account_id"
    type = "S"
  }

  # This table IS the creator identity. Losing it would orphan every published bundle from the key
  # that signed it, and no client would verify them again.
  point_in_time_recovery {
    enabled = true
  }

  tags = merge(local.common_tags, { Name = "${local.publish_name}-creators", Component = "publish" })
}

# Who owns a bundle id. Written with a conditional put, so the first creator to publish an id keeps
# it — this table is the entire squatting and hijack surface.
resource "aws_dynamodb_table" "bundle_owners" {
  name         = "${local.publish_name}-bundle-owners"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "bundle_id"

  attribute {
    name = "bundle_id"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = merge(local.common_tags, { Name = "${local.publish_name}-owners", Component = "publish" })
}

# The mutex over index.json. S3 has no transactions, so two simultaneous publishes would each read
# the index, add a row, and write back — the second silently deleting the first author's bundle.
resource "aws_dynamodb_table" "publish_locks" {
  name         = "${local.publish_name}-locks"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "lock_id"

  attribute {
    name = "lock_id"
    type = "S"
  }

  # A belt-and-braces sweep of leases whose holder died. The service already takes over an expired
  # lease itself (it compares `expires`), so this only stops dead rows accumulating.
  ttl {
    attribute_name = "expires"
    enabled        = true
  }

  tags = merge(local.common_tags, { Name = "${local.publish_name}-locks", Component = "publish" })
}

# ─────────────────────────────── key ───────────────────────────────
#
# Creator private keys are Ed25519 and KMS cannot sign with Ed25519 (its asymmetric keys are RSA and
# NIST ECC). The registry format and every installed client are Ed25519, and changing that would
# mean re-pinning every client ever shipped. So KMS GUARDS the keys instead of holding them:
# encrypted at rest in the table above, decrypted in the function's memory to sign. What that buys
# is that a leaked table dump is worthless without this grant.
resource "aws_kms_key" "publish" {
  description             = "Wraps creator signing keys for ${local.publish_name}"
  deletion_window_in_days = 30
  enable_key_rotation     = false # rotation re-encrypts nothing already stored; see the note above

  tags = merge(local.common_tags, { Name = local.publish_name, Component = "publish" })
}

resource "aws_kms_alias" "publish" {
  name          = "alias/${local.publish_name}"
  target_key_id = aws_kms_key.publish.key_id
}

# ─────────────────────────────── role ───────────────────────────────

resource "aws_iam_role" "publish" {
  name = local.publish_name

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })

  tags = merge(local.common_tags, { Component = "publish" })
}

resource "aws_iam_role_policy_attachment" "publish_logs" {
  role       = aws_iam_role.publish.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Scoped to exactly what one publish does, and nothing else. In particular: no s3:DeleteObject, so
# a bug here cannot unpublish anyone, and no access to any other bucket or table.
resource "aws_iam_role_policy" "publish" {
  name = "${local.publish_name}-access"
  role = aws_iam_role.publish.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetObject"]
        Resource = "${aws_s3_bucket.registry.arn}/*"
      },
      {
        # The intake parking area, and ONLY it: listing is how admission finds a creator's parked
        # uploads, deleting is how a completed (or definitively refused) upload leaves the queue.
        # Delete stays scoped to pending/* — a bug still cannot unpublish anything public.
        Effect    = "Allow"
        Action    = "s3:ListBucket"
        Resource  = aws_s3_bucket.registry.arn
        Condition = { StringLike = { "s3:prefix" = "pending/*" } }
      },
      {
        Effect   = "Allow"
        Action   = "s3:DeleteObject"
        Resource = "${aws_s3_bucket.registry.arn}/pending/*"
      },
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem",
          "dynamodb:DeleteItem", "dynamodb:Scan",
        ]
        Resource = [
          aws_dynamodb_table.creators.arn,
          aws_dynamodb_table.bundle_owners.arn,
          aws_dynamodb_table.publish_locks.arn,
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey"]
        Resource = aws_kms_key.publish.arn
      },
    ]
  })
}

# ─────────────────────────────── function ───────────────────────────────

resource "aws_lambda_function" "publish" {
  count = local.publish_enabled ? 1 : 0

  function_name = local.publish_name
  role          = aws_iam_role.publish.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.publish.repository_url}:${var.publish_image_tag}"
  # ASCII ONLY in anything that becomes an AWS API parameter. EC2 rejects non-ASCII outright
  # (CreateSecurityGroup: "Character sets beyond ASCII are not supported"), and the failure lands
  # mid-apply. Prose with em dashes belongs in the comments, which never leave this file.
  description = "POST /registry/publish - an author's .agentpkg becomes a signed listing plus its installer."

  # Compiling an installer and uploading two artifacts. Well under the 15-minute ceiling, and the
  # index lease (LOCK_TTL_SECONDS) is deliberately longer so an overrun cannot let two publishes
  # into the index at once.
  timeout     = var.publish_timeout_seconds
  memory_size = var.publish_memory_mb

  # /tmp is the only writable path: the uploaded package, the payload, and makensis all live there.
  ephemeral_storage {
    size = 1024
  }

  # DELIBERATELY NOT IN THE VPC, unlike scheduled-jobs next door.
  #
  # This VPC has public subnets, no NAT gateway and no VPC endpoints. A Lambda ENI never receives a
  # public IP, so an in-VPC function here has NO route to the AWS APIs. scheduled-jobs survives that
  # because it only calls `accounts` by its private service-discovery name; this one must reach S3,
  # DynamoDB and KMS on every request, so in-VPC it would hang until the timeout on every publish —
  # a five-minute failure with nothing in the logs but a timeout.
  #
  # The alternatives were VPC endpoints (S3 and DynamoDB gateways are free, a KMS interface endpoint
  # is ~$7/month/AZ) or a NAT gateway (~$32/month). Outside the VPC costs nothing, cold-starts
  # faster, and reaches `accounts` over the SAME public ALB the desktop client already signs in
  # through — so the author's token takes a path it already takes, and no new class of exposure
  # appears. (Both hops are plaintext until TLS lands; that is one fix, not two.)

  environment {
    variables = {
      ACCOUNTS_URL         = local.publish_product_accounts_url
      REGISTRY_BUCKET      = aws_s3_bucket.registry.bucket
      REGISTRY_PUBLIC_BASE = "https://${aws_s3_bucket.registry.bucket}.s3.${var.region}.amazonaws.com"
      CREATORS_TABLE       = aws_dynamodb_table.creators.name
      BUNDLE_OWNERS_TABLE  = aws_dynamodb_table.bundle_owners.name
      LOCKS_TABLE          = aws_dynamodb_table.publish_locks.name
      KMS_KEY_ID           = aws_kms_key.publish.arn
      LOCK_TTL_SECONDS     = tostring(var.publish_timeout_seconds * 2)
      # Products this service builds are HOSTED: they sign into this same platform, so an agent
      # bought from the marketplace works on our keys with no setup.
      PRODUCT_ACCOUNTS_URL    = local.publish_product_accounts_url
      PRODUCT_MODEL_PROXY_URL = local.publish_product_proxy_url
      # Empty => the engine is read from the registry's own `engine` block, which is the normal
      # path: publish an engine once and every stub built afterwards follows it.
      AGENTD_ENGINE_URL     = var.publish_engine_url
      AGENTD_ENGINE_SHA256  = var.publish_engine_sha256
      AGENTD_ENGINE_VERSION = var.publish_engine_version
      # Who may call /registry/admin/* (admit, revoke, pending). Account ids and/or emails,
      # matched case-insensitively. EMPTY MEANS NOBODY - the admin door fails closed on a
      # deployment that never configured admins, rather than opening to anyone signed in.
      ADMIN_IDENTITIES = join(",", var.publish_admin_identities)
      PENDING_PREFIX   = "pending"
      WEB_HOST         = local.publish_web_host
      LOG_LEVEL        = "INFO"
    }
  }

  tags = merge(local.common_tags, { Name = local.publish_name, Component = "publish" })
}

# No security group and no AWSLambdaVPCAccessExecutionRole here on purpose: both exist only to
# serve a `vpc_config`, and this function has none (see the note on the resource above).

# ─────────────────────────────── route ───────────────────────────────
#
# Its own listener port, matching how every other service is exposed here. A path rule on the
# existing `web` listener would work too, and is the natural move once TLS and a domain land.

# The ALB's own security group opens one port per entry in `local.services` (see security.tf), and
# the publish listener is not a service in that map — it is a Lambda. Without this rule the listener
# exists and simply never answers: a connection TIMEOUT, not a 502, which looks like the function is
# broken rather than the port being shut.
resource "aws_vpc_security_group_ingress_rule" "alb_publish" {
  count = local.publish_enabled ? 1 : 0

  security_group_id = aws_security_group.alb.id
  description       = "publish service from anywhere"
  ip_protocol       = "tcp"
  from_port         = var.publish_listener_port
  to_port           = var.publish_listener_port
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_lb_target_group" "publish" {
  count = local.publish_enabled ? 1 : 0

  name        = substr("${local.publish_name}-tg", 0, 32)
  target_type = "lambda"
  # A .agentpkg is uploaded through this: the ALB must not buffer-limit it away.
  lambda_multi_value_headers_enabled = false

  tags = merge(local.common_tags, { Name = local.publish_name, Component = "publish" })
}

resource "aws_lambda_permission" "publish_alb" {
  count = local.publish_enabled ? 1 : 0

  statement_id  = "AllowExecutionFromALB"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.publish[0].function_name
  principal     = "elasticloadbalancing.amazonaws.com"
  source_arn    = aws_lb_target_group.publish[0].arn
}

resource "aws_lb_target_group_attachment" "publish" {
  count = local.publish_enabled ? 1 : 0

  target_group_arn = aws_lb_target_group.publish[0].arn
  target_id        = aws_lambda_function.publish[0].arn
  depends_on       = [aws_lambda_permission.publish_alb]
}

resource "aws_lb_listener" "publish" {
  count = local.publish_enabled && length(aws_lb.main) > 0 ? 1 : 0

  load_balancer_arn = aws_lb.main[0].arn
  port              = var.publish_listener_port
  protocol          = local.tls_enabled ? "HTTPS" : "HTTP"
  ssl_policy        = local.tls_enabled ? "ELBSecurityPolicy-TLS13-1-2-2021-06" : null
  certificate_arn   = local.tls_enabled ? var.certificate_arn : null

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.publish[0].arn
  }
}

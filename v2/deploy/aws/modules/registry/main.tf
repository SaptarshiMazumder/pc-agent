# modules/registry — the PUBLIC marketplace registry: a plain S3 bucket serving the static
# files `agentd bundle index` produces (index.json + *.agentpkg). No CloudFront, no server:
# the S3 REST endpoint is already HTTPS, and INTEGRITY is enforced client-side — RegistryClient
# verifies each download's sha256 and (with a pinned publisher_key) its ed25519 signature,
# fail-closed. A CDN/custom domain is a later drop-in: only the registry_url in the desktop
# flavors changes.
#
# Publishing is manual by design (the operator runs every aws command):
#   python v2/deploy/registry/publish.py --key <keypair> --bucket <this bucket>

terraform {
  required_providers {
    aws    = { source = "hashicorp/aws" }
    random = { source = "hashicorp/random" }
  }
}

locals {
  name_prefix = "${var.project}-${var.environment}"
  common_tags = {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

# S3 bucket names are GLOBALLY unique — a random suffix keeps the name collision-proof
# without encoding an account id.
resource "random_id" "suffix" {
  byte_length = 4
}

resource "aws_s3_bucket" "registry" {
  bucket        = "${local.name_prefix}-registry-${random_id.suffix.hex}"
  force_destroy = true # dev: registry contents are always re-publishable build artifacts
  tags          = local.common_tags
}

# World-READ is the point (anyone's desktop app downloads agents from here); writes stay
# owner-only. Both knobs below are required — AWS blocks public policies by default.
resource "aws_s3_bucket_public_access_block" "registry" {
  bucket = aws_s3_bucket.registry.id

  block_public_acls       = true # ACLs stay blocked; access is via the bucket POLICY only
  ignore_public_acls      = true
  block_public_policy     = false
  restrict_public_buckets = false
}

resource "aws_s3_bucket_policy" "public_read" {
  bucket     = aws_s3_bucket.registry.id
  depends_on = [aws_s3_bucket_public_access_block.registry]

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "PublicReadRegistry"
      Effect    = "Allow"
      Principal = "*"
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.registry.arn}/*"
    }]
  })
}

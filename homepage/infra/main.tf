# =============================================================================
# agentd homepage — a static site on S3, served by CloudFront.
#
# DELIBERATELY SEPARATE from v2/infra. The homepage has no dependency on the
# platform (no ALB, no VPC, no services) and must stay up when the platform is
# paused, so it gets its own state and its own lifecycle. Nothing here reads or
# writes the platform's resources.
#
# SHAPE: one private bucket, one distribution, origin access control between
# them. The bucket has exactly one reader — this distribution — so it is never
# made public; a second, uncached public address for the same page would only be
# a way to serve it without our headers.
#
# CERTIFICATE: a distribution's own *.cloudfront.net name ships with AWS's
# certificate, so this is live over https on the first apply. Attaching a domain
# later is an in-place update (aliases + viewer_certificate) — the distribution,
# and any URL already shared, survives it.
#
# COST: inside CloudFront's perpetual free tier (1 TB out, 10M requests/month)
# plus cents of S3 for a bundle well under 1 MB. Expect well under $1/month at
# ordinary traffic.
# =============================================================================

terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Local state by default, matching v2/infra/environments/dev. Uncomment and
  # fill in to share state across machines or CI.
  # backend "s3" {
  #   bucket = "..."
  #   key    = "homepage/terraform.tfstate"
  #   region = "ap-northeast-1"
  # }
}

provider "aws" {
  region = var.region
}

# CloudFront reads certificates ONLY from us-east-1, whatever region the bucket
# lives in. Used solely when a custom domain is configured.
provider "aws" {
  alias  = "cloudfront"
  region = "us-east-1"
}

locals {
  # NO ENVIRONMENT IN THE NAME, deliberately. This site has no environments to be
  # in: it makes no API calls, reads no config, and the built bundle is identical
  # wherever it lands. There is one site and one state, so an `-staging`/`-prod`
  # suffix would be claiming a distinction that does not exist.
  name = "agentd-homepage"

  common_tags = {
    Project   = "agentd"
    Component = "homepage"
    ManagedBy = "terraform"
  }

  # Decidable at plan time — `aliases` and `viewer_certificate` read this.
  custom_domain = var.domain_name != "" && var.certificate_arn != ""
}

resource "random_id" "suffix" {
  byte_length = 4
}

# ────────────────────────────── the site bucket ──────────────────────────────

resource "aws_s3_bucket" "site" {
  bucket = "${local.name}-${random_id.suffix.hex}"
  # every object is a rebuildable artifact — `npm run build` reproduces all of it
  force_destroy = true
  tags          = local.common_tags
}

resource "aws_s3_bucket_public_access_block" "site" {
  bucket                  = aws_s3_bucket.site.id
  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "site" {
  bucket = aws_s3_bucket.site.id
  versioning_configuration {
    # a bad deploy is one `aws s3 sync` away from being undone
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "site" {
  bucket = aws_s3_bucket.site.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# ─────────────────────────── cloudfront distribution ──────────────────────────

# How CloudFront proves it is CloudFront to a private bucket.
resource "aws_cloudfront_origin_access_control" "site" {
  name                              = local.name
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# The bucket trusts the SERVICE plus THIS EXACT distribution — not the service at
# large, which would let any other account's distribution read this bucket.
data "aws_iam_policy_document" "site" {
  statement {
    sid       = "AllowCloudFrontRead"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.site.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.site.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "site" {
  bucket = aws_s3_bucket.site.id
  policy = data.aws_iam_policy_document.site.json
}

# AWS-managed policies, referenced by id rather than hardcoded ARNs.
data "aws_cloudfront_cache_policy" "optimized" {
  name = "Managed-CachingOptimized"
}

data "aws_cloudfront_response_headers_policy" "security" {
  name = "Managed-SecurityHeadersPolicy"
}

resource "aws_cloudfront_distribution" "site" {
  enabled             = true
  is_ipv6_enabled     = true
  comment             = local.name
  default_root_object = "index.html"
  price_class         = var.price_class
  aliases             = local.custom_domain ? [var.domain_name] : []
  tags                = local.common_tags

  origin {
    domain_name              = aws_s3_bucket.site.bucket_regional_domain_name
    origin_id                = "site"
    origin_access_control_id = aws_cloudfront_origin_access_control.site.id
  }

  default_cache_behavior {
    target_origin_id       = "site"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true

    # Per-object Cache-Control set at upload time (see scripts/deploy.sh) is what
    # actually decides freshness: hashed assets are immutable for a year,
    # index.html revalidates every request.
    cache_policy_id            = data.aws_cloudfront_cache_policy.optimized.id
    response_headers_policy_id = data.aws_cloudfront_response_headers_policy.security.id
  }

  # A single-page site with in-page anchors: anything unresolved is a mistyped
  # path, and the homepage is a better answer than an XML error document.
  custom_error_response {
    error_code            = 403
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 10
  }

  custom_error_response {
    error_code            = 404
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 10
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = local.custom_domain ? false : true
    acm_certificate_arn            = local.custom_domain ? var.certificate_arn : null
    ssl_support_method             = local.custom_domain ? "sni-only" : null
    minimum_protocol_version       = local.custom_domain ? "TLSv1.2_2021" : null
  }
}

# ───────────────────────────────────── dns ────────────────────────────────────
#
# An EXPLICIT record for this hostname, published into the platform's zone.
#
# WHY THIS WORKS DESPITE THE WILDCARD. That zone already aliases `*.<root_domain>`
# at the ALB (v2/infra/modules/dns.tf), so without this the homepage hostname would
# resolve to the web app. Route 53 answers from the most specific match, and an
# explicit record always beats a wildcard — so naming the host here is exactly what
# takes it off the ALB and onto this distribution. Nothing in v2/infra changes.
#
# A and AAAA both, because the distribution has IPv6 enabled and alias records to
# CloudFront are not billed as queries.

resource "aws_route53_record" "site" {
  for_each = var.hosted_zone_id != "" && local.custom_domain ? toset(["A", "AAAA"]) : toset([])

  zone_id = var.hosted_zone_id
  name    = var.domain_name
  type    = each.value

  alias {
    name                   = aws_cloudfront_distribution.site.domain_name
    zone_id                = aws_cloudfront_distribution.site.hosted_zone_id
    evaluate_target_health = false
  }
}

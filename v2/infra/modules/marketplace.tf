# THE PUBLIC MARKETPLACE — a page anyone can visit, with no account and no agentd.
#
# WHY THERE IS NO SERVICE HERE. Browsing a store is reading one JSON file. The publish service
# already writes `catalog.json` beside `index.json` (see domain/catalog.py), with the creator
# names resolved off the signed roster and every url absolute — so the page has nothing to
# compute and nothing to ask. A container in front of that would add cost, a deploy, and an
# outage mode to something S3 already does with eleven nines of durability.
#
# ONE DISTRIBUTION, TWO ORIGINS, and that is the whole trick:
#
#     /             -> the site bucket      the page itself
#     /catalog.json -> the registry bucket  what to render
#     /index.json   -> the registry bucket  the signed record, for anyone who wants to check
#
# The browser sees ONE origin, so there is no CORS configuration anywhere and no preflight on the
# only request this page makes. Artifacts (.agentpkg, installers) are NOT routed here: the catalog
# gives absolute urls straight to the bucket, which is exactly what the desktop client has always
# done, so nothing about downloading changes.
#
# NO CERTIFICATE REQUIRED. A distribution's own *.cloudfront.net name comes with AWS's
# certificate, so this is live over https on the first apply. Attaching a domain later is an
# in-place update (aliases + viewer_certificate below) — the distribution, and therefore any URL
# already shared, survives it.
#
# COST: within CloudFront's perpetual free tier (1 TB out, 10M requests/month) plus cents of S3.
# Deliberately NOT gated on `paused`/`hibernate`: those switches turn off compute, and a
# storefront that goes dark whenever the backend is asleep is a funnel that leaks. It costs
# effectively nothing to leave standing.

locals {
  marketplace_name = "${local.name_prefix}-marketplace"

  # An alias is only legal with a certificate that covers it, and a certificate is only useful
  # once something is aliased to it. One local so the two can never be set half-way.
  marketplace_custom_domain = (
    var.marketplace_domain_name != "" && var.marketplace_certificate_arn != ""
  )
}

# ─────────────────────────────── the site bucket ───────────────────────────────
#
# PRIVATE, unlike the registry bucket next door. The registry is public because installed clients
# fetch from it directly by url; the site has exactly one reader (this distribution) and giving it
# a second public address would mean a second, uncached, unheadered way to serve the same page.

resource "aws_s3_bucket" "marketplace" {
  bucket        = "${local.marketplace_name}-${random_id.suffix.hex}"
  force_destroy = true # every object is a build artifact, re-uploadable in one command
  tags          = merge(local.common_tags, { Component = "marketplace" })
}

resource "aws_s3_bucket_public_access_block" "marketplace" {
  bucket                  = aws_s3_bucket.marketplace.id
  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = true
  restrict_public_buckets = true
}

# How CloudFront proves it is CloudFront to a private bucket. The bucket policy below trusts the
# SERVICE plus this exact distribution — not the service at large, which would let any other
# account's distribution read this bucket.
resource "aws_cloudfront_origin_access_control" "marketplace" {
  name                              = local.marketplace_name
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_s3_bucket_policy" "marketplace" {
  bucket     = aws_s3_bucket.marketplace.id
  depends_on = [aws_s3_bucket_public_access_block.marketplace]

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowThisDistribution"
      Effect    = "Allow"
      Principal = { Service = "cloudfront.amazonaws.com" }
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.marketplace.arn}/*"
      Condition = {
        StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.marketplace.arn }
      }
    }]
  })
}

# ─────────────────────────────── caching ───────────────────────────────
#
# AWS's managed policies, by name rather than by id: the ids are global constants that read as
# magic numbers, and the names say what they do.

data "aws_cloudfront_cache_policy" "optimized" {
  name = "Managed-CachingOptimized"
}

data "aws_cloudfront_cache_policy" "disabled" {
  name = "Managed-CachingDisabled"
}

# The headers a static page cannot set for itself. `frame-ancestors` in particular is header-only
# — a browser IGNORES it in a <meta> tag — so this is the only place clickjacking protection for
# the marketplace can actually live.
resource "aws_cloudfront_response_headers_policy" "marketplace" {
  name = local.marketplace_name

  security_headers_config {
    content_type_options { override = true }
    frame_options {
      frame_option = "DENY"
      override     = true
    }
    referrer_policy {
      referrer_policy = "strict-origin-when-cross-origin"
      override        = true
    }
    # HSTS only with a custom domain. On *.cloudfront.net it would pin a policy onto a hostname
    # shared with every other AWS customer's distribution, which is not ours to set.
    dynamic "strict_transport_security" {
      for_each = local.marketplace_custom_domain ? [1] : []
      content {
        access_control_max_age_sec = 31536000
        include_subdomains         = true
        override                   = true
      }
    }
  }
}

# ─────────────────────────────── the distribution ───────────────────────────────

resource "aws_cloudfront_distribution" "marketplace" {
  enabled             = true
  is_ipv6_enabled     = true
  comment             = "${local.marketplace_name} - public agent marketplace"
  default_root_object = "index.html"
  price_class         = var.marketplace_price_class

  # NO custom_error_response, on purpose. The usual SPA trick (404 -> /index.html, 200) is
  # distribution-wide, so it would also rewrite a MISSING catalog.json into an HTML page served
  # with a 200 — and the page, which handles a 404 as "nothing published yet", would instead
  # report that the registry returned something that is not a catalog. The listing is one route
  # today; when it grows per-agent pages, rewrite them with a CloudFront Function scoped to the
  # site behaviour rather than an error mapping that reaches the registry's paths too.

  aliases = local.marketplace_custom_domain ? [var.marketplace_domain_name] : []

  origin {
    origin_id                = "site"
    domain_name              = aws_s3_bucket.marketplace.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.marketplace.id
  }

  origin {
    origin_id = "registry"
    # No access control: this bucket is public by design (installed clients fetch it directly by
    # url), so CloudFront reads it the same way they do. Locking it behind OAC would be a
    # padlock on a door with no wall.
    domain_name = aws_s3_bucket.registry.bucket_regional_domain_name
  }

  default_cache_behavior {
    target_origin_id       = "site"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true
    # Vite fingerprints every asset, so a long cache is safe; index.html is small and revalidates.
    cache_policy_id            = data.aws_cloudfront_cache_policy.optimized.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.marketplace.id
  }

  # The registry's two documents. CACHING OFF rather than short: both are rewritten on every
  # publish, and the failure mode of a cached one is a store that shows an agent as missing for
  # hours after its author successfully published it — the exact bug the `no-cache` header on the
  # objects already guards against. They are a few KB; there is nothing to save here.
  dynamic "ordered_cache_behavior" {
    for_each = toset(["/catalog.json", "/index.json"])
    content {
      path_pattern               = ordered_cache_behavior.value
      target_origin_id           = "registry"
      viewer_protocol_policy     = "redirect-to-https"
      allowed_methods            = ["GET", "HEAD"]
      cached_methods             = ["GET", "HEAD"]
      compress                   = true
      cache_policy_id            = data.aws_cloudfront_cache_policy.disabled.id
      response_headers_policy_id = aws_cloudfront_response_headers_policy.marketplace.id
    }
  }

  restrictions {
    geo_restriction { restriction_type = "none" }
  }

  viewer_certificate {
    # The default certificate is AWS's own, on *.cloudfront.net — which is why this deployment
    # needs no ACM certificate to be live over https.
    cloudfront_default_certificate = local.marketplace_custom_domain ? null : true
    acm_certificate_arn            = local.marketplace_custom_domain ? var.marketplace_certificate_arn : null
    ssl_support_method             = local.marketplace_custom_domain ? "sni-only" : null
    minimum_protocol_version       = local.marketplace_custom_domain ? "TLSv1.2_2021" : null
  }

  tags = merge(local.common_tags, { Name = local.marketplace_name, Component = "marketplace" })
}

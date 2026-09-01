# DNS + certificates — everything `var.root_domain` turns on. Empty root_domain = this whole
# file is zero resources, which is every environment that predates the domain.
#
# THE SHAPE OF THE NAMESPACE (one wildcard level, deliberately):
#
#   <root>                 the web client (ALB default action -> web)      A alias -> ALB
#   *.<root>               published agents: <bundle-id>.<root> serves     A alias -> ALB
#                          that agent (gateway derives the id from Host;
#                          bundle_owners in publish.tf IS the namespace)
#   platform.<root>        Cloud Agent Builder — an ENTRY IN agent_hostnames, not a special
#                          case; the wildcard record resolves it, an explicit ALB rule routes it
#   admin.<root>           the admin console — resolved by the wildcard record, routed to the
#                          web image by an explicit ALB rule, told apart by nginx server_name
#   marketplace.<root>     CloudFront (its own record; a specific name beats the wildcard)
#
# A wildcard certificate covers exactly ONE label, so *.<root> covers all of the above and
# nothing deeper — <agent>.<org>.<root> is a future certificate, not a future record.
#
# TWO CERTIFICATES FOR ONE NAME SET, because AWS requires it: the ALB needs the cert in ITS
# region, CloudFront reads certs from us-east-1 and nowhere else. Both are DNS-validated into
# the zone below, so once the registrar's nameservers point here, issuance is automatic —
# including every future renewal, which is the actual reason to prefer DNS validation.

locals {
  dns_managed = var.root_domain != ""
}

resource "aws_route53_zone" "main" {
  count = local.dns_managed ? 1 : 0

  name = var.root_domain
  tags = local.common_tags
}

# ─────────────────────────────── certificates ───────────────────────────────

resource "aws_acm_certificate" "alb" {
  count = local.dns_managed ? 1 : 0

  domain_name               = var.root_domain
  subject_alternative_names = ["*.${var.root_domain}"]
  validation_method         = "DNS"

  lifecycle {
    # A cert in use by a listener cannot be destroyed first; renewal/replacement must overlap.
    create_before_destroy = true
  }

  tags = local.common_tags
}

# The same names again, in the one region CloudFront accepts. Consumed by marketplace.tf.
resource "aws_acm_certificate" "cloudfront" {
  count    = local.dns_managed ? 1 : 0
  provider = aws.us_east_1

  domain_name               = var.root_domain
  subject_alternative_names = ["*.${var.root_domain}"]
  validation_method         = "DNS"

  lifecycle {
    create_before_destroy = true
  }

  tags = local.common_tags
}

# Validation records. The apex and the wildcard validate through the SAME record (ACM hands out
# identical name/value for both), and the two certificates ask for the same records too — so
# `allow_overwrite` is load-bearing: without it the second writer of an identical record fails
# the apply with "record already exists".
resource "aws_route53_record" "alb_cert_validation" {
  for_each = {
    for dvo in(local.dns_managed ? aws_acm_certificate.alb[0].domain_validation_options : []) :
    dvo.domain_name => {
      name   = dvo.resource_record_name
      type   = dvo.resource_record_type
      record = dvo.resource_record_value
    }
  }

  zone_id         = aws_route53_zone.main[0].zone_id
  name            = each.value.name
  type            = each.value.type
  records         = [each.value.record]
  ttl             = 300
  allow_overwrite = true
}

resource "aws_route53_record" "cloudfront_cert_validation" {
  for_each = {
    for dvo in(local.dns_managed ? aws_acm_certificate.cloudfront[0].domain_validation_options : []) :
    dvo.domain_name => {
      name   = dvo.resource_record_name
      type   = dvo.resource_record_type
      record = dvo.resource_record_value
    }
  }

  zone_id         = aws_route53_zone.main[0].zone_id
  name            = each.value.name
  type            = each.value.type
  records         = [each.value.record]
  ttl             = 300
  allow_overwrite = true
}

# These two are what "the certificate exists" means to the rest of the module: they complete
# only when ACM has ISSUED (a listener refuses a PENDING_VALIDATION cert), which in turn
# happens only after the registrar's nameservers point at the zone. First apply on a fresh
# domain therefore WAITS here — that is the pause during which you flip the nameservers
# (DOMAIN-SETUP.md), not an error.
resource "aws_acm_certificate_validation" "alb" {
  count = local.dns_managed ? 1 : 0

  certificate_arn         = aws_acm_certificate.alb[0].arn
  validation_record_fqdns = [for r in aws_route53_record.alb_cert_validation : r.fqdn]

  timeouts {
    create = "60m"
  }
}

resource "aws_acm_certificate_validation" "cloudfront" {
  count    = local.dns_managed ? 1 : 0
  provider = aws.us_east_1

  certificate_arn         = aws_acm_certificate.cloudfront[0].arn
  validation_record_fqdns = [for r in aws_route53_record.cloudfront_cert_validation : r.fqdn]

  timeouts {
    create = "60m"
  }
}

# ─────────────────────────────── records ───────────────────────────────

# The apex and the wildcard, both at the ALB. TWO records and not five: platform/admin/every
# published agent all resolve through the wildcard — which hostname means what is decided at
# the ALB (host rules, alb.tf) and in nginx/the daemon, so publishing an agent never touches
# DNS. Absent while hibernating, because there is no ALB to point at; the zone and certs stay,
# so coming back is an apply, not a re-validation.
resource "aws_route53_record" "apex" {
  count = local.dns_managed && local.alb_count == 1 ? 1 : 0

  zone_id = aws_route53_zone.main[0].zone_id
  name    = var.root_domain
  type    = "A"

  alias {
    name                   = aws_lb.main[0].dns_name
    zone_id                = aws_lb.main[0].zone_id
    evaluate_target_health = false
  }
}

resource "aws_route53_record" "wildcard" {
  count = local.dns_managed && local.alb_count == 1 ? 1 : 0

  zone_id = aws_route53_zone.main[0].zone_id
  name    = "*.${var.root_domain}"
  type    = "A"

  alias {
    name                   = aws_lb.main[0].dns_name
    zone_id                = aws_lb.main[0].zone_id
    evaluate_target_health = false
  }
}

# marketplace.<root> -> CloudFront. Its own record because its target is not the ALB; a
# specific name always beats the wildcard, so this simply carves one label out of it. NOT
# gated on hibernate — the distribution deliberately outlives the compute (marketplace.tf).
resource "aws_route53_record" "marketplace" {
  count = local.dns_managed ? 1 : 0

  zone_id = aws_route53_zone.main[0].zone_id
  name    = "marketplace.${var.root_domain}"
  type    = "A"

  alias {
    name                   = aws_cloudfront_distribution.marketplace.domain_name
    zone_id                = aws_cloudfront_distribution.marketplace.hosted_zone_id
    evaluate_target_health = false
  }
}

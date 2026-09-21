# ADDITIONAL DOMAINS — names this deployment answers to that are NOT the platform's own.
#
# WHY THIS EXISTS. `root_domain` is not merely a hostname: it is the zone, both certificates,
# the per-agent wildcard, the admin console, and — through `public_host` — the TOKEN ISSUER
# stamped into every access token and checked by the daemon and the model proxy. Setting it to
# a product's domain therefore renames the whole platform after one product, which is what
# happened on 2026-09-21 when comfypenguin.com became root_domain: the issuer read
# `comfypenguin.com:4100`, the admin console moved, and the web client lost its hostname
# entirely because the wildcard swallowed every name that was left.
#
# A DOMAIN HERE IS JUST A NAME THAT RESOLVES AND TERMINATES TLS. It gets a zone, a certificate,
# and a place on the load balancer's 443 listener. It gets NO wildcard, NO admin console, NO
# marketplace record and no say over the issuer — `agent_hostnames` decides what it serves, and
# everything platform-shaped stays on root_domain where it belongs.
#
# ONE LISTENER, MANY CERTIFICATES. An ALB holds a default certificate plus any number of extras
# and picks between them by SNI, which every browser has sent for a decade. So this needs no
# second load balancer, no second address, and no per-domain cost beyond the zone's $0.50.
#
# THE MANUAL STEP IS THE SAME ONE root_domain HAS: point the registrar's nameservers at the
# zone this creates (`extra_domain_name_servers`), or ACM cannot validate and the apply waits.

locals {
  # Keyed by the domain itself, so adding or removing one never renumbers the others — a list
  # index would make "remove the first of three" read as "replace all three".
  extra_domains = { for d in var.extra_domains : d => d }
}

resource "aws_route53_zone" "extra" {
  for_each = local.extra_domains

  name = each.value
  tags = merge(local.common_tags, { Name = each.value })
}

# REGIONAL, for the ALB. The CloudFront certificate that root_domain mints has no counterpart
# here on purpose: the marketplace lives on one address under the platform's own name, and a
# product domain pointing at someone else's storefront would be a surprise, not a feature.
resource "aws_acm_certificate" "extra" {
  for_each = local.extra_domains

  domain_name = each.value
  # The bare name only. A wildcard would hand every subdomain of a product's domain to the
  # daemon's published-agent route, which is the platform's behaviour leaking into a name that
  # is not the platform's — see the header.
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }

  tags = merge(local.common_tags, { Name = each.value })
}

# One validation record per (domain, option). Flattened because `domain_validation_options` is
# a set per certificate and for_each needs one flat map across all of them.
resource "aws_route53_record" "extra_cert_validation" {
  for_each = {
    for pair in flatten([
      for domain, cert in aws_acm_certificate.extra : [
        for dvo in cert.domain_validation_options : {
          key    = "${domain}|${dvo.domain_name}"
          domain = domain
          name   = dvo.resource_record_name
          record = dvo.resource_record_value
          type   = dvo.resource_record_type
        }
      ]
    ]) : pair.key => pair
  }

  zone_id         = aws_route53_zone.extra[each.value.domain].zone_id
  name            = each.value.name
  type            = each.value.type
  records         = [each.value.record]
  ttl             = 60
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "extra" {
  for_each = local.extra_domains

  certificate_arn = aws_acm_certificate.extra[each.key].arn
  validation_record_fqdns = [
    for k, r in aws_route53_record.extra_cert_validation : r.fqdn if startswith(k, "${each.key}|")
  ]
}

# ── the name on the load balancer ────────────────────────────────────────────
#
# EVERY TLS LISTENER, not just 443, and that is not thoroughness for its own sake. This product
# speaks on several ports: the daemon's WebSocket is :8787, accounts is :4100, the model proxy
# :4000. A browser on an extra domain opens a socket to that domain on 8787, and a listener
# holding only root_domain's certificate answers it with the wrong name — which a browser
# refuses outright. Certifying 443 alone gives a page that loads and then cannot connect, which
# is a worse failure than not loading, because everything looks fine.
#
# Attached as an EXTRA certificate, never as the default. The default stays root_domain's: it is
# what an SNI-less client and any unmatched name still get, and replacing it would move the
# platform's own identity onto a product's certificate.
resource "aws_lb_listener_certificate" "extra" {
  for_each = local.alb_count == 1 && local.tls_enabled ? {
    for pair in setproduct(keys(local.extra_domains), keys(local.alb_services)) :
    "${pair[0]}|${pair[1]}" => { domain = pair[0], service = pair[1] }
  } : {}

  listener_arn    = aws_lb_listener.svc[each.value.service].arn
  certificate_arn = aws_acm_certificate_validation.extra[each.value.domain].certificate_arn
}

# The name itself, pointed at the load balancer. No wildcard sibling: a product domain serves
# exactly what `agent_hostnames` says it serves and nothing else.
resource "aws_route53_record" "extra_apex" {
  for_each = local.alb_count == 1 ? local.extra_domains : {}

  zone_id = aws_route53_zone.extra[each.key].zone_id
  name    = each.value
  type    = "A"

  alias {
    name                   = aws_lb.main[0].dns_name
    zone_id                = aws_lb.main[0].zone_id
    evaluate_target_health = false
  }
}

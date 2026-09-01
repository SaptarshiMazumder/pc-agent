# The public front door. One internet-facing load balancer, plus (per service) a target
# group (the pool of containers to forward to) and a listener (which incoming port
# forwards to which target group) — all driven by the services map.
#
# TLS note: listeners are HTTP-only for the private-testing phase. When a domain + ACM cert
# land, add a :443 listener with host-based rules to these same target groups — the port
# listeners here can then become redirects. (Deliberately structured so that lands additively.)

# ADDING `count` RENAMES A RESOURCE, AND TERRAFORM WILL DESTROY THE OLD ONE FOR YOU.
#
# `aws_lb.main` and `aws_lb.main[0]` are different addresses. Introducing `count` without saying
# so moves the resource, and Terraform reads that as one resource disappearing and another
# appearing: it destroyed a live load balancer and immediately tried to create its replacement.
# AWS does not release an ELB's NAME the instant deletion returns, so the create failed with
# "ELBv2 Load Balancer (agentd-dev) already exists" — leaving no ALB at all and no state entry.
# (Recovering was a second `apply`; the damage was a changed DNS name and some downtime.)
#
# These `moved` blocks are what should have shipped with the `count`. They are harmless when the
# old address is absent — which it now is — and they matter if state is ever restored from
# terraform.tfstate.backup, where the un-indexed address still lives.
moved {
  from = aws_lb.main
  to   = aws_lb.main[0]
}

# The load balancer itself — internet-facing, spread across the public subnets.
resource "aws_lb" "main" {
  # Gone entirely while hibernating (var.hibernate) — that is the ~$18/month this environment's
  # cost switch is aimed at, and the reason the hostname is not stable across a hibernate.
  count = local.alb_count

  name               = local.name_prefix
  load_balancer_type = "application"
  internal           = false
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id
  idle_timeout       = 4000 # keep long-lived WebSocket connections (daemon) alive
  tags               = local.common_tags
}

# One target group per service — the pool the ALB forwards to.
# target_type = "ip" because Fargate tasks register by IP address (awsvpc networking).
resource "aws_lb_target_group" "svc" {
  for_each = local.alb_services

  name        = "${local.name_prefix}-${each.key}"
  port        = each.value.port
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  # How long a REPLACED task keeps draining before ECS may stop it. The AWS default is 300s,
  # and the old deployment is not gone until every one of its targets finishes draining — so
  # that default silently put a 5-minute floor under EVERY rollout and under the deploy
  # workflow's `ecs wait services-stable`. 30s is ample for these request lifetimes; the one
  # exception is the daemon, whose WebSocket sessions are long-lived and deserve a real drain.
  deregistration_delay = each.value.deregistration_delay

  health_check {
    path                = each.value.health_path
    matcher             = "200-399"
    interval            = 30
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  lifecycle {
    create_before_destroy = true
  }

  tags = local.common_tags
}

locals {
  # TLS is a per-environment fact, expressed once. Everything below (and outputs.tf) reads this
  # rather than re-testing the variable, so "is this environment encrypted" has one answer.
  # Two ways to be on: a certificate handed in (`certificate_arn`, the original path) or a
  # domain this module manages itself (`root_domain`, dns.tf — which mints and validates one).
  # Both are plain variables, so every `count` that reads this stays decidable at plan time.
  tls_enabled = var.certificate_arn != "" || local.dns_managed

  # Which certificate the listeners actually present. The explicit variable wins so an
  # environment that brings its own cert is untouched; otherwise the managed one — referenced
  # through its VALIDATION resource, so no listener can come up holding a cert ACM has not
  # issued yet (a listener with a PENDING_VALIDATION cert is an apply-time error).
  listener_certificate_arn = (
    var.certificate_arn != "" ? var.certificate_arn : (
      local.dns_managed ? aws_acm_certificate_validation.alb[0].certificate_arn : null
    )
  )

  # Which PORT each service listens on publicly. Identical to the container port in every case
  # but one: with TLS on, `web` moves to 443 so the app has a normal https:// URL with no port in
  # it, and :80 becomes a redirect (below). The TARGET GROUP still points at the container's own
  # port — a listener port and a target port are different things, and conflating them here would
  # send :443 traffic to a container that is not listening on 443.
  listener_ports = {
    for name, cfg in local.alb_services :
    name => (name == "web" && local.tls_enabled ? 443 : cfg.port)
  }
}

# One listener per service — "traffic arriving on THIS port forwards to THAT group."
resource "aws_lb_listener" "svc" {
  for_each = local.alb_services

  load_balancer_arn = aws_lb.main[0].arn
  port              = local.listener_ports[each.key]
  protocol          = local.tls_enabled ? "HTTPS" : "HTTP"
  # A modern policy rather than the AWS default: this terminates sign-in traffic, and the default
  # still negotiates TLS 1.0/1.1 with clients that ask.
  ssl_policy      = local.tls_enabled ? "ELBSecurityPolicy-TLS13-1-2-2021-06" : null
  certificate_arn = local.tls_enabled ? local.listener_certificate_arn : null

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.svc[each.key].arn
  }
}

# ── host rules on the WEB listener ──────────────────────────────────────────────────────────────
#
# The web listener (:443 with TLS) is the environment's front door once a domain exists: the apex
# is its default action (the web UI), and everything else under the domain is a HOST RULE here.
# Priorities are deliberate and ordered most-specific-first — a rule list, not a coincidence:
#
#   1..N   agent_hostnames  explicit vanity hosts -> the daemon (platform.<root> lives here)
#   40     admin_hostname   -> the web image (nginx server_name serves admin.html at its root).
#                            Exists ONLY to outrank the wildcard below, which would otherwise
#                            send the console's hostname to the daemon.
#   50     *.<root>         every other label -> the daemon, which derives the agent id from the
#                            Host header (AGENTD_APP_HOST_SUFFIX). ONE rule for every published
#                            agent, forever — a rule per agent would hit the ALB's ~100-rule
#                            ceiling before the marketplace hit its second page.
#
# All of these forward to target groups that ALSO keep their own port listeners (:8787 etc.) —
# the ports are how pre-domain clients and service-to-service calls arrive, and nothing here
# takes that away.

resource "aws_lb_listener_rule" "agent_host" {
  # keys() is sorted, so each hostname's priority is stable across applies.
  for_each = local.alb_count == 1 ? var.agent_hostnames : {}

  listener_arn = aws_lb_listener.svc["web"].arn
  priority     = 1 + index(sort(keys(var.agent_hostnames)), each.key)

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.svc["daemon"].arn
  }

  condition {
    host_header {
      values = [each.key]
    }
  }

  tags = local.common_tags
}

resource "aws_lb_listener_rule" "admin_host" {
  count = local.alb_count == 1 && var.admin_hostname != "" ? 1 : 0

  listener_arn = aws_lb_listener.svc["web"].arn
  priority     = 40

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.svc["web"].arn
  }

  condition {
    host_header {
      values = [var.admin_hostname]
    }
  }

  tags = local.common_tags
}

resource "aws_lb_listener_rule" "wildcard_agent_host" {
  count = local.alb_count == 1 && local.dns_managed ? 1 : 0

  listener_arn = aws_lb_listener.svc["web"].arn
  priority     = 50

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.svc["daemon"].arn
  }

  condition {
    host_header {
      values = ["*.${var.root_domain}"]
    }
  }

  tags = local.common_tags
}

# :80 -> :443, only when TLS is on (without it, :80 IS the web listener above).
#
# A redirect and not a second forward: serving the same app over both schemes means a bookmark,
# a stale link, or a typed hostname silently downgrades the connection, and the user has no way
# to tell. 301 so browsers stop asking.
resource "aws_lb_listener" "http_redirect" {
  count = local.tls_enabled && !var.hibernate ? 1 : 0

  load_balancer_arn = aws_lb.main[0].arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

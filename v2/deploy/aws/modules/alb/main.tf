# modules/alb — the public front door. One internet-facing load balancer, plus (per exposed
# service) a target group (the pool of containers to forward to) and a listener (which
# incoming port forwards to which target group).
#
# Exposed: web (:80 UI), accounts (:4100 sign-in), daemon (:8787 WebSocket), and gateway
# (:4000 model proxy — public for desktop platform-keys mode; per-user custom auth guards it.
# The cloud daemon still reaches it internally via service discovery).
#
# TLS note: listeners are HTTP-only for the private-testing phase. When a domain + ACM cert
# land, add a :443 listener with host-based rules to these same target groups — the port
# listeners here can then become redirects. (Deliberately structured so that lands additively.)

locals {
  name_prefix = "${var.project}-${var.environment}"
  common_tags = {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

# The load balancer itself — internet-facing, spread across the public subnets.
resource "aws_lb" "main" {
  name               = local.name_prefix
  load_balancer_type = "application"
  internal           = false
  security_groups    = [var.alb_sg_id]
  subnets            = var.subnet_ids
  idle_timeout       = 4000 # keep long-lived WebSocket connections (daemon) alive
  tags               = local.common_tags
}

# One target group per exposed service — the pool the ALB forwards to.
# target_type = "ip" because Fargate tasks register by IP address (awsvpc networking).
resource "aws_lb_target_group" "svc" {
  for_each = var.services

  name        = "${local.name_prefix}-${each.key}"
  port        = each.value.port
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"

  health_check {
    path                = each.value.health_path
    matcher             = "200-399"
    interval            = 30
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  tags = local.common_tags
}

# One listener per exposed service — "traffic arriving on THIS port forwards to THAT group."
resource "aws_lb_listener" "svc" {
  for_each = var.services

  load_balancer_arn = aws_lb.main.arn
  port              = each.value.port
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.svc[each.key].arn
  }
}

# The public front door. One internet-facing load balancer, plus (per service) a target
# group (the pool of containers to forward to) and a listener (which incoming port
# forwards to which target group) — all driven by the services map.
#
# TLS note: listeners are HTTP-only for the private-testing phase. When a domain + ACM cert
# land, add a :443 listener with host-based rules to these same target groups — the port
# listeners here can then become redirects. (Deliberately structured so that lands additively.)

# The load balancer itself — internet-facing, spread across the public subnets.
resource "aws_lb" "main" {
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
  for_each = local.services

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

# One listener per service — "traffic arriving on THIS port forwards to THAT group."
resource "aws_lb_listener" "svc" {
  for_each = local.services

  load_balancer_arn = aws_lb.main.arn
  port              = each.value.port
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.svc[each.key].arn
  }
}

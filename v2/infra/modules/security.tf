# The firewalls (security groups). Four groups:
#   • alb     — public; one hole per service port (driven by the services map)
#   • service — the containers; reachable ONLY from the ALB, plus from each other
#   • rds     — Postgres; reachable ONLY from the containers
#   • efs     — file system; reachable ONLY from the containers
# Security groups are STATEFUL: a reply to an allowed request is automatically allowed back.

# ── ALB: the public front door ──
resource "aws_security_group" "alb" {
  name        = "${local.name_prefix}-alb"
  description = "Public ALB: HTTP from the internet"
  vpc_id      = aws_vpc.main.id
  tags        = merge(local.common_tags, { Name = "${local.name_prefix}-alb" })
}

# One internet-facing hole per service. All 4 are public today — web (UI), accounts
# (sign-in), daemon (WebSocket), and gateway (model proxy, public for desktop
# platform-keys mode; per-user custom auth guards it).
resource "aws_vpc_security_group_ingress_rule" "alb_ingress" {
  for_each = var.services

  security_group_id = aws_security_group.alb.id
  description       = "${each.key} from anywhere"
  ip_protocol       = "tcp"
  from_port         = each.value.port
  to_port           = each.value.port
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "alb_all" {
  security_group_id = aws_security_group.alb.id
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

# ── Fargate tasks (the containers) ──
resource "aws_security_group" "service" {
  name        = "${local.name_prefix}-service"
  description = "Fargate tasks: from the ALB, and from each other"
  vpc_id      = aws_vpc.main.id
  tags        = merge(local.common_tags, { Name = "${local.name_prefix}-service" })
}

# Each service's port is reachable from the ALB (the ALB forwards to it).
resource "aws_vpc_security_group_ingress_rule" "svc_from_alb" {
  for_each = var.services

  security_group_id            = aws_security_group.service.id
  description                  = "${each.key} from ALB"
  ip_protocol                  = "tcp"
  from_port                    = each.value.port
  to_port                      = each.value.port
  referenced_security_group_id = aws_security_group.alb.id
}

resource "aws_vpc_security_group_ingress_rule" "svc_from_self" {
  security_group_id            = aws_security_group.service.id
  description                  = "inter-service traffic (daemon to gateway/accounts)"
  ip_protocol                  = "tcp"
  from_port                    = 0
  to_port                      = 65535
  referenced_security_group_id = aws_security_group.service.id
}

resource "aws_vpc_security_group_egress_rule" "svc_all" {
  security_group_id = aws_security_group.service.id
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

# ── RDS (Postgres): only from the tasks, on 5432 ──
resource "aws_security_group" "rds" {
  name        = "${local.name_prefix}-rds"
  description = "Postgres: from the Fargate tasks only"
  vpc_id      = aws_vpc.main.id
  tags        = merge(local.common_tags, { Name = "${local.name_prefix}-rds" })
}

resource "aws_vpc_security_group_ingress_rule" "rds_from_service" {
  security_group_id            = aws_security_group.rds.id
  description                  = "postgres from services"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = aws_security_group.service.id
}

# ── EFS: NFS (2049) only from the tasks ──
resource "aws_security_group" "efs" {
  name        = "${local.name_prefix}-efs"
  description = "EFS NFS: from the Fargate tasks only"
  vpc_id      = aws_vpc.main.id
  tags        = merge(local.common_tags, { Name = "${local.name_prefix}-efs" })
}

resource "aws_vpc_security_group_ingress_rule" "efs_from_service" {
  security_group_id            = aws_security_group.efs.id
  description                  = "NFS from services"
  ip_protocol                  = "tcp"
  from_port                    = 2049
  to_port                      = 2049
  referenced_security_group_id = aws_security_group.service.id
}

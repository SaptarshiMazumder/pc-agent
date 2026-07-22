# modules/security — the firewalls (security groups). Four groups:
#   • alb     — public, HTTP from anywhere
#   • service — the 4 containers; reachable ONLY from the ALB, plus from each other
#   • rds     — Postgres; reachable ONLY from the containers
#   • efs     — file system; reachable ONLY from the containers
# Security groups are STATEFUL: a reply to an allowed request is automatically allowed back.

locals {
  name_prefix = "${var.project}-${var.environment}"
  common_tags = {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

# ── ALB: the public front door ──
resource "aws_security_group" "alb" {
  name        = "${local.name_prefix}-alb"
  description = "Public ALB: HTTP from the internet"
  vpc_id      = var.vpc_id
  tags        = merge(local.common_tags, { Name = "${local.name_prefix}-alb" })
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  security_group_id = aws_security_group.alb.id
  description       = "web (UI) from anywhere"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_ingress_rule" "alb_accounts" {
  security_group_id = aws_security_group.alb.id
  description       = "accounts API from anywhere"
  ip_protocol       = "tcp"
  from_port         = 4100
  to_port           = 4100
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_ingress_rule" "alb_daemon" {
  security_group_id = aws_security_group.alb.id
  description       = "daemon WebSocket from anywhere"
  ip_protocol       = "tcp"
  from_port         = 8787
  to_port           = 8787
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "alb_all" {
  security_group_id = aws_security_group.alb.id
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

# ── Fargate tasks (the 4 containers) ──
resource "aws_security_group" "service" {
  name        = "${local.name_prefix}-service"
  description = "Fargate tasks: from the ALB, and from each other"
  vpc_id      = var.vpc_id
  tags        = merge(local.common_tags, { Name = "${local.name_prefix}-service" })
}

resource "aws_vpc_security_group_ingress_rule" "svc_web_from_alb" {
  security_group_id            = aws_security_group.service.id
  description                  = "web from ALB"
  ip_protocol                  = "tcp"
  from_port                    = 80
  to_port                      = 80
  referenced_security_group_id = aws_security_group.alb.id
}

resource "aws_vpc_security_group_ingress_rule" "svc_daemon_from_alb" {
  security_group_id            = aws_security_group.service.id
  description                  = "daemon (ws) from ALB"
  ip_protocol                  = "tcp"
  from_port                    = 8787
  to_port                      = 8787
  referenced_security_group_id = aws_security_group.alb.id
}

resource "aws_vpc_security_group_ingress_rule" "svc_accounts_from_alb" {
  security_group_id            = aws_security_group.service.id
  description                  = "accounts from ALB"
  ip_protocol                  = "tcp"
  from_port                    = 4100
  to_port                      = 4100
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
  vpc_id      = var.vpc_id
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
  vpc_id      = var.vpc_id
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

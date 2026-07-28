# ─────────────────────────────────────────────────────────────────────────────
# STATE MIGRATION: old per-concern modules → the single flat stack module.
# Every deployed resource keeps its real AWS object; only its Terraform ADDRESS
# changes (module.network.* → module.stack.*). `terraform plan` must report ONLY
# moves — 0 to add, 0 to destroy (plus a few description-only rule updates).
# Dev is the only applied environment, so only dev carries this file.
# Safe to delete once the move has been applied once.
# ─────────────────────────────────────────────────────────────────────────────

# ── network ──
moved {
  from = module.network.aws_vpc.main
  to   = module.stack.aws_vpc.main
}
moved {
  from = module.network.aws_subnet.public
  to   = module.stack.aws_subnet.public
}
moved {
  from = module.network.aws_internet_gateway.main
  to   = module.stack.aws_internet_gateway.main
}
moved {
  from = module.network.aws_route_table.public
  to   = module.stack.aws_route_table.public
}
moved {
  from = module.network.aws_route_table_association.public
  to   = module.stack.aws_route_table_association.public
}

# ── security ──
moved {
  from = module.security.aws_security_group.alb
  to   = module.stack.aws_security_group.alb
}
moved {
  from = module.security.aws_security_group.service
  to   = module.stack.aws_security_group.service
}
moved {
  from = module.security.aws_security_group.rds
  to   = module.stack.aws_security_group.rds
}
moved {
  from = module.security.aws_security_group.efs
  to   = module.stack.aws_security_group.efs
}

# The four hand-written per-port ALB holes became one for_each keyed by service name.
moved {
  from = module.security.aws_vpc_security_group_ingress_rule.alb_http
  to   = module.stack.aws_vpc_security_group_ingress_rule.alb_ingress["web"]
}
moved {
  from = module.security.aws_vpc_security_group_ingress_rule.alb_accounts
  to   = module.stack.aws_vpc_security_group_ingress_rule.alb_ingress["accounts"]
}
moved {
  from = module.security.aws_vpc_security_group_ingress_rule.alb_daemon
  to   = module.stack.aws_vpc_security_group_ingress_rule.alb_ingress["daemon"]
}
moved {
  from = module.security.aws_vpc_security_group_ingress_rule.alb_gateway
  to   = module.stack.aws_vpc_security_group_ingress_rule.alb_ingress["gateway"]
}

# Same for the ALB→service holes.
moved {
  from = module.security.aws_vpc_security_group_ingress_rule.svc_web_from_alb
  to   = module.stack.aws_vpc_security_group_ingress_rule.svc_from_alb["web"]
}
moved {
  from = module.security.aws_vpc_security_group_ingress_rule.svc_accounts_from_alb
  to   = module.stack.aws_vpc_security_group_ingress_rule.svc_from_alb["accounts"]
}
moved {
  from = module.security.aws_vpc_security_group_ingress_rule.svc_daemon_from_alb
  to   = module.stack.aws_vpc_security_group_ingress_rule.svc_from_alb["daemon"]
}
moved {
  from = module.security.aws_vpc_security_group_ingress_rule.svc_gateway_from_alb
  to   = module.stack.aws_vpc_security_group_ingress_rule.svc_from_alb["gateway"]
}

moved {
  from = module.security.aws_vpc_security_group_ingress_rule.svc_from_self
  to   = module.stack.aws_vpc_security_group_ingress_rule.svc_from_self
}
moved {
  from = module.security.aws_vpc_security_group_ingress_rule.rds_from_service
  to   = module.stack.aws_vpc_security_group_ingress_rule.rds_from_service
}
moved {
  from = module.security.aws_vpc_security_group_ingress_rule.efs_from_service
  to   = module.stack.aws_vpc_security_group_ingress_rule.efs_from_service
}
moved {
  from = module.security.aws_vpc_security_group_egress_rule.alb_all
  to   = module.stack.aws_vpc_security_group_egress_rule.alb_all
}
moved {
  from = module.security.aws_vpc_security_group_egress_rule.svc_all
  to   = module.stack.aws_vpc_security_group_egress_rule.svc_all
}

# ── iam ──
moved {
  from = module.iam.aws_iam_role.execution
  to   = module.stack.aws_iam_role.execution
}
moved {
  from = module.iam.aws_iam_role_policy_attachment.execution_managed
  to   = module.stack.aws_iam_role_policy_attachment.execution_managed
}
moved {
  from = module.iam.aws_iam_role_policy.execution_secrets
  to   = module.stack.aws_iam_role_policy.execution_secrets
}
moved {
  from = module.iam.aws_iam_role.task
  to   = module.stack.aws_iam_role.task
}
moved {
  from = module.iam.aws_iam_role_policy.task_efs
  to   = module.stack.aws_iam_role_policy.task_efs
}

# ── ecr ──
# Legacy chain: the gateway repo pre-existed the module refactor; keep its old hop so
# any state that still has the original address resolves through to the flat one.
moved {
  from = module.gateway_ecr.aws_ecr_repository.this
  to   = module.ecr.aws_ecr_repository.this["gateway"]
}
moved {
  from = module.ecr.aws_ecr_repository.this
  to   = module.stack.aws_ecr_repository.this
}

# ── cluster ──
moved {
  from = module.cluster.aws_ecs_cluster.main
  to   = module.stack.aws_ecs_cluster.main
}
moved {
  from = module.cluster.aws_cloudwatch_log_group.agentd
  to   = module.stack.aws_cloudwatch_log_group.agentd
}
moved {
  from = module.cluster.aws_service_discovery_private_dns_namespace.main
  to   = module.stack.aws_service_discovery_private_dns_namespace.main
}

# ── data ──
moved {
  from = module.data.random_password.master_key
  to   = module.stack.random_password.master_key
}
moved {
  from = module.data.random_password.accounts_internal_key
  to   = module.stack.random_password.accounts_internal_key
}
moved {
  from = module.data.aws_secretsmanager_secret.app
  to   = module.stack.aws_secretsmanager_secret.app
}
moved {
  from = module.data.aws_secretsmanager_secret_version.app
  to   = module.stack.aws_secretsmanager_secret_version.app
}
moved {
  from = module.data.aws_efs_file_system.main
  to   = module.stack.aws_efs_file_system.main
}
moved {
  from = module.data.aws_efs_mount_target.main
  to   = module.stack.aws_efs_mount_target.main
}
moved {
  from = module.data.aws_efs_access_point.data
  to   = module.stack.aws_efs_access_point.data
}

# ── alb ──
moved {
  from = module.alb.aws_lb.main
  to   = module.stack.aws_lb.main
}
moved {
  from = module.alb.aws_lb_target_group.svc
  to   = module.stack.aws_lb_target_group.svc
}
moved {
  from = module.alb.aws_lb_listener.svc
  to   = module.stack.aws_lb_listener.svc
}

# ── registry ──
moved {
  from = module.registry.random_id.suffix
  to   = module.stack.random_id.suffix
}
moved {
  from = module.registry.aws_s3_bucket.registry
  to   = module.stack.aws_s3_bucket.registry
}
moved {
  from = module.registry.aws_s3_bucket_public_access_block.registry
  to   = module.stack.aws_s3_bucket_public_access_block.registry
}
moved {
  from = module.registry.aws_s3_bucket_policy.public_read
  to   = module.stack.aws_s3_bucket_policy.public_read
}

# ── services: 4 module instances → for_each keys ──
moved {
  from = module.svc_gateway.aws_ecs_task_definition.this
  to   = module.stack.aws_ecs_task_definition.svc["gateway"]
}
moved {
  from = module.svc_gateway.aws_service_discovery_service.this
  to   = module.stack.aws_service_discovery_service.svc["gateway"]
}
moved {
  from = module.svc_gateway.aws_ecs_service.this
  to   = module.stack.aws_ecs_service.svc["gateway"]
}

moved {
  from = module.svc_accounts.aws_ecs_task_definition.this
  to   = module.stack.aws_ecs_task_definition.svc["accounts"]
}
moved {
  from = module.svc_accounts.aws_service_discovery_service.this
  to   = module.stack.aws_service_discovery_service.svc["accounts"]
}
moved {
  from = module.svc_accounts.aws_ecs_service.this
  to   = module.stack.aws_ecs_service.svc["accounts"]
}

moved {
  from = module.svc_daemon.aws_ecs_task_definition.this
  to   = module.stack.aws_ecs_task_definition.svc["daemon"]
}
moved {
  from = module.svc_daemon.aws_service_discovery_service.this
  to   = module.stack.aws_service_discovery_service.svc["daemon"]
}
moved {
  from = module.svc_daemon.aws_ecs_service.this
  to   = module.stack.aws_ecs_service.svc["daemon"]
}

moved {
  from = module.svc_web.aws_ecs_task_definition.this
  to   = module.stack.aws_ecs_task_definition.svc["web"]
}
moved {
  from = module.svc_web.aws_service_discovery_service.this
  to   = module.stack.aws_service_discovery_service.svc["web"]
}
moved {
  from = module.svc_web.aws_ecs_service.this
  to   = module.stack.aws_ecs_service.svc["web"]
}

# ── service rename: gateway → model-proxy ──
# These moves connect the old and new Terraform addresses. Resources whose physical AWS
# names contain the service key are replaced with create-before-destroy; follow the staged
# image migration in deploy/LAUNCH.md before the full apply.
moved {
  from = module.stack.aws_vpc_security_group_ingress_rule.alb_ingress["gateway"]
  to   = module.stack.aws_vpc_security_group_ingress_rule.alb_ingress["model-proxy"]
}
moved {
  from = module.stack.aws_vpc_security_group_ingress_rule.svc_from_alb["gateway"]
  to   = module.stack.aws_vpc_security_group_ingress_rule.svc_from_alb["model-proxy"]
}
moved {
  from = module.stack.aws_ecr_repository.this["gateway"]
  to   = module.stack.aws_ecr_repository.this["model-proxy"]
}
moved {
  from = module.stack.aws_lb_target_group.svc["gateway"]
  to   = module.stack.aws_lb_target_group.svc["model-proxy"]
}
moved {
  from = module.stack.aws_lb_listener.svc["gateway"]
  to   = module.stack.aws_lb_listener.svc["model-proxy"]
}
moved {
  from = module.stack.aws_ecs_task_definition.svc["gateway"]
  to   = module.stack.aws_ecs_task_definition.svc["model-proxy"]
}
moved {
  from = module.stack.aws_service_discovery_service.svc["gateway"]
  to   = module.stack.aws_service_discovery_service.svc["model-proxy"]
}
moved {
  from = module.stack.aws_ecs_service.svc["gateway"]
  to   = module.stack.aws_ecs_service.svc["model-proxy"]
}

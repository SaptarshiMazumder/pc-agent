# Handles the ECS services (Phase 4) will need: which cluster to run in, where to log,
# and which DNS namespace to register in.

output "cluster_arn" {
  description = "ECS cluster ARN (services run in it)."
  value       = aws_ecs_cluster.main.arn
}

output "cluster_name" {
  description = "ECS cluster name."
  value       = aws_ecs_cluster.main.name
}

output "log_group_name" {
  description = "CloudWatch log group the containers stream to."
  value       = aws_cloudwatch_log_group.agentd.name
}

output "namespace_arn" {
  description = "Private DNS namespace ARN for service discovery."
  value       = aws_service_discovery_private_dns_namespace.main.arn
}

output "namespace_id" {
  description = "Private DNS namespace id (the service module registers into this)."
  value       = aws_service_discovery_private_dns_namespace.main.id
}

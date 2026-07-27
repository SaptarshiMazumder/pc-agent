output "service_name" {
  description = "The ECS service name."
  value       = aws_ecs_service.this.name
}

output "task_definition_arn" {
  description = "The task definition ARN (its latest revision)."
  value       = aws_ecs_task_definition.this.arn
}

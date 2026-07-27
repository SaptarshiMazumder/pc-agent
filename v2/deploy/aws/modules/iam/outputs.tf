# The role ARNs, which the task definitions (Phase 4) reference.

output "execution_role_arn" {
  description = "Role AWS uses to launch a task (pull image, logs, secrets)."
  value       = aws_iam_role.execution.arn
}

output "task_role_arn" {
  description = "Role the running container code uses (mount EFS)."
  value       = aws_iam_role.task.arn
}

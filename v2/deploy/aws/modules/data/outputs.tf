# Handles the task definitions plug into: the secret ARN to inject and the EFS ids to mount.

output "app_secret_arn" {
  description = "Secrets Manager ARN holding the app secrets (master key + provider keys)."
  value       = aws_secretsmanager_secret.app.arn
}

output "efs_id" {
  description = "EFS file system id."
  value       = aws_efs_file_system.main.id
}

output "efs_access_point_id" {
  description = "EFS access point id (mounted as /data by accounts + daemon)."
  value       = aws_efs_access_point.data.id
}

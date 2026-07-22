# The security-group ids, so later modules (ALB, ECS services, RDS, EFS) can attach to them.

output "alb_sg_id" {
  description = "Security group for the public load balancer."
  value       = aws_security_group.alb.id
}

output "service_sg_id" {
  description = "Security group for the Fargate tasks (the 4 containers)."
  value       = aws_security_group.service.id
}

output "rds_sg_id" {
  description = "Security group for the Postgres database."
  value       = aws_security_group.rds.id
}

output "efs_sg_id" {
  description = "Security group for the EFS file system."
  value       = aws_security_group.efs.id
}

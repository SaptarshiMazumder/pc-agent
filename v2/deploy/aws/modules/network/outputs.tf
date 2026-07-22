# What this module hands back to whoever calls it. Other modules need the VPC id and the
# subnet ids to attach to this network.

output "vpc_id" {
  description = "The VPC id — the security and cluster modules attach to it."
  value       = aws_vpc.main.id
}

output "public_subnet_ids" {
  description = "Both public subnet ids — the ALB and Fargate tasks launch into these."
  value       = aws_subnet.public[*].id
}

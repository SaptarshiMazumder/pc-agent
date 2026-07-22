# The public hostname (your URL) and the target group ARNs the Phase 4 ECS services
# register their containers into.

output "alb_dns_name" {
  description = "Public hostname of the load balancer — THIS is your app's URL."
  value       = aws_lb.main.dns_name
}

output "target_group_arns" {
  description = "Map of service name -> target group ARN. Phase 4's ECS services register into these."
  value       = { for name, tg in aws_lb_target_group.svc : name => tg.arn }
}

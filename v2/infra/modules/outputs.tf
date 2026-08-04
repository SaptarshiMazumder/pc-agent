output "repository_urls" {
  description = "All image push targets (push-images.ps1 reads this)."
  value       = { for name, repo in aws_ecr_repository.this : name => repo.repository_url }
}

output "model_proxy_repo_url" {
  description = "Where to push the Model Proxy image."
  value       = aws_ecr_repository.this["model-proxy"].repository_url
}

output "gateway_repo_url" {
  description = "Deprecated compatibility alias for model_proxy_repo_url."
  value       = aws_ecr_repository.this["model-proxy"].repository_url
}

output "app_url" {
  description = "The public URL of the app (serves once the containers are healthy)."
  value       = "http://${aws_lb.main.dns_name}"
}

# ── Desktop-flavor wiring: these three values go into the flavors' distribution.toml ──

output "accounts_url" {
  description = "[platform] accounts_url for the desktop flavors (sign-in endpoint)."
  value       = "http://${aws_lb.main.dns_name}:4100"
}

output "model_proxy_url" {
  description = "[platform] model_proxy_url for the desktop flavors (platform keys)."
  value       = "http://${aws_lb.main.dns_name}:4000"
}

output "model_gateway_url" {
  description = "Deprecated compatibility alias for model_proxy_url."
  value       = "http://${aws_lb.main.dns_name}:4000"
}

output "registry_url" {
  description = "[store] registry_url for the desktop flavors (public marketplace index)."
  value       = "https://${aws_s3_bucket.registry.bucket}.s3.${var.region}.amazonaws.com/index.json"
}

output "registry_bucket" {
  description = "Upload target for deploy/registry/publish.py (aws s3 sync)."
  value       = aws_s3_bucket.registry.bucket
}

output "alerts_topic_arn" {
  description = "SNS topic every alarm publishes to. Check subscriptions are CONFIRMED, not PendingConfirmation."
  value       = aws_sns_topic.alerts.arn
}

output "alarm_names" {
  description = "Every alarm created, for `aws cloudwatch describe-alarms --alarm-names`. Any of these stuck in INSUFFICIENT_DATA after traffic has flowed means its metric-math search matched nothing."
  value = concat(
    [
      aws_cloudwatch_metric_alarm.unbilled_spend.alarm_name,
      aws_cloudwatch_metric_alarm.ledger_write_failures.alarm_name,
      aws_cloudwatch_metric_alarm.ledger_buffer_backlog.alarm_name,
      aws_cloudwatch_metric_alarm.cap_overspend.alarm_name,
      aws_cloudwatch_metric_alarm.accounts_unreachable.alarm_name,
      aws_cloudwatch_metric_alarm.proxy_5xx.alarm_name,
      aws_cloudwatch_metric_alarm.cost_per_hour.alarm_name,
      aws_cloudwatch_metric_alarm.resolve_latency.alarm_name,
      aws_cloudwatch_metric_alarm.login_rejections.alarm_name,
    ],
    [for a in aws_cloudwatch_metric_alarm.unhealthy_targets : a.alarm_name],
    [for a in aws_cloudwatch_metric_alarm.no_successful_logins : a.alarm_name],
  )
}

output "dashboard_urls" {
  description = "Direct links to the two dashboards. Service health during an incident; business daily."
  value = {
    service_health = "https://${var.region}.console.aws.amazon.com/cloudwatch/home?region=${var.region}#dashboards:name=${aws_cloudwatch_dashboard.service_health.dashboard_name}"
    business       = "https://${var.region}.console.aws.amazon.com/cloudwatch/home?region=${var.region}#dashboards:name=${aws_cloudwatch_dashboard.business.dashboard_name}"
  }
}

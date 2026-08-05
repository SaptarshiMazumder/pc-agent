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

output "ingest_url" {
  description = "[platform] ingest_url for the desktop flavors and the web build (opt-in client telemetry). Empty in a client's config = the uploader stays off."
  value       = "http://${aws_lb.main.dns_name}:${local.services["ingest"].port}"
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
    [aws_cloudwatch_metric_alarm.scheduled_jobs_failing.alarm_name],
    [for a in aws_cloudwatch_metric_alarm.scheduled_jobs_absent : a.alarm_name],
  )
}

output "scheduled_jobs_function" {
  description = "The Lambda every schedule invokes. Run a job on demand with `aws lambda invoke --function-name <this> --payload '{\"job\":\"manual\",\"path\":\"/ledger/snapshot\"}' --cli-binary-format raw-in-base64-out out.json` — the same payload the clock sends."
  value       = aws_lambda_function.scheduled_jobs.function_name
}

output "scheduled_jobs" {
  description = "Every schedule: when it fires and what it calls, AFTER this environment's overrides — reading var.scheduled_jobs here would report the default cadence rather than the one deployed. `aws scheduler list-schedules` confirms they exist and are ENABLED."
  value = {
    for name, job in local.scheduled_jobs : aws_scheduler_schedule.job[name].name => {
      path     = job.path
      schedule = "${job.schedule} ${var.scheduled_job_timezone}"
      enabled  = job.enabled
    }
  }
}

output "scheduled_jobs_log_group" {
  description = "Where each scheduled run's result is logged (job, outcome, status, result)."
  value       = aws_cloudwatch_log_group.scheduled_jobs.name
}

output "dashboard_urls" {
  description = "Direct links to the two dashboards. Service health during an incident; business daily."
  value = {
    service_health = "https://${var.region}.console.aws.amazon.com/cloudwatch/home?region=${var.region}#dashboards:name=${aws_cloudwatch_dashboard.service_health.dashboard_name}"
    business       = "https://${var.region}.console.aws.amazon.com/cloudwatch/home?region=${var.region}#dashboards:name=${aws_cloudwatch_dashboard.business.dashboard_name}"
  }
}

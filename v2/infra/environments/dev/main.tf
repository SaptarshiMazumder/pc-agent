# ─────────────────────────────────────────────────────────────────────────────
# ENVIRONMENT: dev — a root module. This folder IS the environment: its own state
# file, one instantiation of the shared module (../../modules), pass-through
# outputs. All per-env differences go on the module call below; every resource
# declaration lives in modules/.
# ─────────────────────────────────────────────────────────────────────────────

terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }
}

provider "aws" {
  region = "ap-northeast-1"
}

variable "model_proxy_desired_count" {
  description = "Initial Model Proxy task count; use 0 during the one-time gateway rename."
  type        = number
  default     = 1
}

variable "alert_email" {
  description = "Where dev alarms go. Kept as a variable rather than hardcoded so the address is not committed; pass with -var or a *.tfvars file. Empty = topic created, nobody subscribed."
  type        = string
  default     = ""
}

module "stack" {
  source = "../../modules"

  environment = "dev"
  # dev conveniences (already the stack defaults, spelled out for contrast with prod):
  image_tag_mutability      = "MUTABLE"
  ecr_force_delete          = true
  model_proxy_desired_count = var.model_proxy_desired_count

  # Alarms (3.5). Thresholds are deliberately loose for dev: the goal here is to prove the
  # alarms WIRE UP and can actually fire, not to tune them. The money alarms (unbilled
  # spend, ledger failures, buffer backlog, overspend) all trigger at > 0 and need no
  # tuning at any traffic level -- those are the ones that matter.
  alert_email             = var.alert_email
  cost_per_hour_alarm_usd = 5
  proxy_5xx_threshold     = 5
  # enable_login_absence_alarm stays false: dev has no continuous traffic, so "no sign-ins
  # for 30 minutes" is the normal state overnight.
}

# ── Pass-through outputs (push-images.ps1 and the desktop flavors read these) ──

output "repository_urls" {
  description = "All image push targets."
  value       = module.stack.repository_urls
}

output "model_proxy_repo_url" {
  description = "Where to push the model-proxy image."
  value       = module.stack.model_proxy_repo_url
}

# Deprecated output kept for deployment scripts that have not migrated yet.
output "gateway_repo_url" {
  description = "Deprecated alias for model_proxy_repo_url."
  value       = module.stack.model_proxy_repo_url
}

output "app_url" {
  description = "The public URL of the app."
  value       = module.stack.app_url
}

output "accounts_url" {
  description = "[platform] accounts_url for the desktop flavors."
  value       = module.stack.accounts_url
}

output "model_proxy_url" {
  description = "[platform] model_proxy_url for the desktop flavors."
  value       = module.stack.model_proxy_url
}

# Deprecated output kept for existing flavor-generation automation.
output "model_gateway_url" {
  description = "Deprecated alias for model_proxy_url."
  value       = module.stack.model_proxy_url
}

output "registry_url" {
  description = "[store] registry_url for the desktop flavors."
  value       = module.stack.registry_url
}

output "registry_bucket" {
  description = "Upload target for deploy/registry/publish.py."
  value       = module.stack.registry_bucket
}

# ── The clock (monitoring/scheduler_check.ps1 reads these three) ──

output "scheduled_jobs_function" {
  description = "Lambda that runs the accounts service's scheduled endpoints; invoke it by hand to test a job."
  value       = module.stack.scheduled_jobs_function
}

output "scheduled_jobs" {
  description = "Every schedule: when it fires and what it calls."
  value       = module.stack.scheduled_jobs
}

output "scheduled_jobs_log_group" {
  description = "Where each scheduled run's result is logged."
  value       = module.stack.scheduled_jobs_log_group
}

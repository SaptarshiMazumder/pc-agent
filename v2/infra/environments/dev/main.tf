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

# The cost switch. `terraform apply -var paused=true` to stop the compute bill; a plain
# `terraform apply` to bring it back. Keeps the ALB, so the hostname every desktop installer and
# the web image have baked in never changes. See the variable's own docs in ../../modules.
variable "paused" {
  description = "Scale every task to 0 and disable the scheduled jobs, keeping the ALB (and therefore the URL)."
  type        = bool
  default     = false
}

# Everything `paused` does, plus the load balancer itself: ~$3/month instead of ~$21. The cost is
# that the URL is different on the way back up, so the web image needs a rebuild (merge to
# develop, or run Deploy). Desktop flavors re-sync themselves on `npm run dev`.
variable "hibernate" {
  description = "Remove the ALB, its listeners/target groups and the ECS services too. Implies paused. The public URL WILL change on the next apply."
  type        = bool
  default     = false
}

# The publish service's image tag. EMPTY creates only its ECR repo, tables and KMS key — an
# image-based Lambda cannot be created before its image exists, so a single apply would fail
# halfway. Apply once, push an image, then set this (`-var publish_image_tag=v1`, or put it in
# dev.auto.tfvars so it sticks) and apply again.
variable "publish_image_tag" {
  description = "Image tag for the publish Lambda. Empty = do not create the function yet."
  type        = string
  default     = ""
}

# Who may admit/revoke creators through the publish service's admin endpoints. A variable rather
# than a literal so the operator's email is not committed; put it in dev.auto.tfvars. Empty =
# the admin door refuses everyone (fail-closed) and only the offline --root-key flow works.
variable "publish_admin_identities" {
  description = "Registry admins for the publish service: account ids and/or emails."
  type        = list(string)
  default     = []
}

module "stack" {
  source = "../../modules"

  environment = "dev"
  paused      = var.paused
  # Publishing (see modules/publish.tf and deploy/PUBLISH-SERVICE.md).
  publish_image_tag        = var.publish_image_tag
  publish_admin_identities = var.publish_admin_identities
  hibernate                = var.hibernate
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

  # The clock, slowed down for dev. Renewals are HOURLY by default and that is correct in
  # production, where a subscription must never renew more than an hour late. Dev has no
  # subscribers and is scaled to zero for most of the day, so the production cadence buys
  # nothing and invokes a Lambda against a dead service 24 times a day -- noise in the logs
  # and in the scheduled-jobs-failing alarm.
  #
  # 00:00 keeps the ORDERING the module depends on: renewals :00 -> close-expired 00:05 ->
  # snapshot 00:20, so the snapshot still reports a settled balance sheet. This is the same
  # schedule as the default, just its once-daily instance rather than all 24.
  scheduled_job_overrides = {
    subscription-renewals = { schedule = "cron(0 0 * * ? *)" }
  }

  # THE MARKETPLACE's trust anchor: the public half of the keypair that signs index.json. The
  # hosted daemon pins downloads to it, so browsers get the same guarantee the desktop already
  # has (whose installer bakes this identical string into distribution.toml).
  #
  # Public by nature — it can only VERIFY, never sign — which is why it lives in git next to the
  # flavors rather than in Secrets Manager. The private half never leaves the publisher: a local
  # file for `agentd bundle publish`, a repo secret for the publish-registry workflow.
  #
  # It must match `publisher_key` in v2/clients/desktop/flavors/*/distribution.toml. A mismatch
  # is silent until someone installs, then reads as "the bundle is corrupt".
  registry_publisher_key = "gYM/XoS5CZo1yNAdW2Ai4HwnLNDlJhl/nvJUh5TavFY="
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

output "ingest_url" {
  description = "[platform] ingest_url for the desktop flavors and the web build (opt-in client telemetry). sync-platform-urls.mjs reads this."
  value       = module.stack.ingest_url
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

# ── the publish service (modules/publish.tf) ────────────────────────────────────────────

output "publish_ecr_repository" {
  description = "Push the publish image here, then apply again with -var publish_image_tag=<tag>."
  value       = module.stack.publish_ecr_repository
}

output "publish_url" {
  description = "[store] publish_url for the desktop flavors - where Agent Builder's Publish button posts. sync-platform-urls.mjs reads this. Empty until publish_image_tag is set."
  value       = module.stack.publish_url
}

output "publish_creators_table" {
  description = "Creator identities awaiting admission; `agentd bundle roster pending/admit` reads it."
  value       = module.stack.publish_creators_table
}

output "publish_kms_key" {
  description = "KMS alias for `agentd bundle roster upload-root --kms-key`."
  value       = module.stack.publish_kms_key
}

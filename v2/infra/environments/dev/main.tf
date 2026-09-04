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

  # REMOTE STATE, same story as staging's block below: dev is deployed from more than one
  # machine, and state on somebody's laptop means whoever has the laptop is the only one who
  # can change anything. Commented until the migration is done from the checkout that holds
  # dev's REAL state (state files elsewhere are partial): run `infra/bootstrap` once, then
  # uncomment and `terraform init -migrate-state` FROM THAT MACHINE.
  #
  # backend "s3" {
  #   bucket  = "agentd-tfstate-<account-id>"
  #   key     = "dev/terraform.tfstate"
  #   region  = "ap-northeast-1"
  #   encrypt = true
  # }
}

provider "aws" {
  region = "ap-northeast-1"
}

# CloudFront-region provider — dns.tf mints the marketplace certificate in us-east-1 because
# CloudFront reads certificates from nowhere else. The module REQUIRES this alias to be passed
# (configuration_aliases in modules/providers.tf) even when root_domain is empty.
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
}

variable "accounts_desired_count" {
  description = "How many accounts tasks to run. Honoured only when accounts_external_database is set — SQLite on one file cannot have two writers. 2 is what makes a deploy or an AZ failure invisible rather than a short outage."
  type        = number
  default     = 2
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
variable "builder_image_tag" {
  description = "Image tag in the builder ECR repo. Empty = no builder Lambda (agent window builds then fail on hosted, loudly)."
  type        = string
  default     = ""
}

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

# WHICH ENGINE A PUBLISHED STUB INSTALLS. A per-agent installer is a ~200 KB stub that downloads
# the ~250 MB engine on a machine that has none, so it needs a url and a digest baked in at the
# moment the stub is built. Normally these stay EMPTY and the publish service reads the registry's
# own signed `engine` row — but writing that row needs the publisher's private key, which lives
# only in CI (one key, one place, no copy). These three are the operator's way to point stubs at an
# engine WITHOUT that key: the digest travels in the stub itself, so it is verified at install time
# either way. Set them in dev.auto.tfvars so they stick across applies.
variable "publish_engine_url" {
  description = "Absolute URL of the engine installer a stub downloads. Empty = read the registry index."
  type        = string
  default     = ""
}

variable "publish_engine_sha256" {
  description = "sha256 of that installer. Required whenever publish_engine_url is set — a stub refuses a download it cannot verify."
  type        = string
  default     = ""
}

variable "publish_engine_version" {
  description = "The engine version that installer installs (used for a payload's minimum-version check)."
  type        = string
  default     = ""
}

# ── a domain for the public marketplace (optional; modules/marketplace.tf) ──────────────
#
# Both empty = the marketplace is live on its distribution's own *.cloudfront.net address, over
# https, with no certificate of ours. Set BOTH to move it to a domain; the distribution is
# updated in place, so the address already shared keeps working.

variable "marketplace_domain_name" {
  description = "Hostname for the public marketplace. Needs marketplace_certificate_arn too."
  type        = string
  default     = ""
}

variable "marketplace_certificate_arn" {
  description = "ACM certificate for that hostname. MUST be in us-east-1 - CloudFront reads them from nowhere else, and this is NOT the regional cert the ALB uses."
  type        = string
  default     = ""
}


# ── the domain (../../DOMAIN-SETUP.md is the runbook) ───────────────────────────────────────
#
# ONE SUBDOMAIN PER ENVIRONMENT. Dev owns `dev.<apex>` and staging owns `staging.<apex>`, each
# with its own Route 53 zone, its own pair of ACM certificates and its own wildcard — so an
# environment's namespace is a subtree nothing else can reach into, and the two can never fight
# over a record. The bare apex is nobody's: it holds the NS delegations for those two zones and
# serves nothing.
#
# WHY THESE CARRY REAL VALUES INSTEAD OF "" + a gitignored tfvars: a hostname is not a secret,
# and the environment that a checkout deploys should be legible from the checkout. A local
# `dev.auto.tfvars` still overrides them — WHICH IS THE TRAP: tfvars beat defaults, so an old
# `root_domain = "thorgodofthunder.site"` line there silently keeps this environment on the
# apex. Delete that line before applying (see the runbook's step 0).
variable "root_domain" {
  description = "The environment's base domain. Non-empty = the module manages Route 53 + ACM + HTTPS + the per-agent wildcard."
  type        = string
  default     = "dev.thorgodofthunder.site"
}

# The full hostname is the KEY (not a label): the module writes it into the ALB host rule and
# into the daemon's AGENTD_APP_HOSTS from this one map, so the two cannot disagree.
variable "agent_hostnames" {
  description = "Vanity hostname -> agent id (ALB host rule + AGENTD_APP_HOSTS, one map so they cannot disagree)."
  type        = map(string)
  default = {
    "platform.dev.thorgodofthunder.site" = "cloud-agent-builder"
  }
}

variable "admin_hostname" {
  description = "The standalone admin console's hostname (nginx server_name + the ALB rule that shields it from the wildcard)."
  type        = string
  default     = "admin.dev.thorgodofthunder.site"
}

variable "cost_per_hour_alarm_usd" {
  description = "Spend-rate alarm. Dev's default is deliberately loose — the goal is proving the alarm wires up, not tuning it; staging carries production's value."
  type        = number
  default     = 5
}

variable "proxy_5xx_threshold" {
  description = "Model-proxy 5xx alarm threshold, as a percentage over 5 minutes. Loose in dev, production's value in staging."
  type        = number
  default     = 5
}

variable "scheduled_job_overrides" {
  description = <<-EOT
    Per-job schedule overrides. Dev's DEFAULT slows subscription renewals to once a day:
    hourly is correct in production, but dev has no subscribers and is scaled to zero for
    most of the day, so the production cadence just invokes a Lambda against a dead service
    24 times a day. 00:00 keeps the ordering the module depends on (renewals :00 ->
    close-expired 00:05 -> snapshot 00:20) — the same schedule as the default, just its
    once-daily instance. Staging overrides nothing and rehearses the production clock.
  EOT
  type        = any
  default = {
    subscription-renewals = { schedule = "cron(0 0 * * ? *)" }
  }
}

variable "resolve_latency_p99_ms" {
  description = "Page when p99 session-token resolution exceeds this. It runs before every model call for every user, so it is the platform's latency floor."
  type        = number
  default     = 1000
}

variable "login_rejection_threshold" {
  description = "Rejected sign-ins in 5 minutes before paging. Catches credential stuffing and a broken password path alike."
  type        = number
  default     = 20
}

variable "enable_login_absence_alarm" {
  description = "Page when NO successful sign-in occurs in the window. Off in dev: it is legitimately silent for hours, and this is the one alarm that treats missing data as breaching."
  type        = bool
  default     = false
}

variable "enable_job_absence_alarm" {
  description = "Page when NO scheduled job has run in 24h (a stopped billing clock). Off by default because it necessarily fires once before the first invocation; enable after the schedules have run."
  type        = bool
  default     = false
}

variable "checkout_return_origins" {
  description = "Origins /me/checkout may return a paying customer to (AGENTD_CHECKOUT_RETURN_ORIGINS). Empty = any absolute http(s) URL — right for dev, an open redirect wearing our domain in production."
  type        = list(string)
  default     = []
}

variable "payment_provider" {
  description = <<-EOT
    Which payment rail this environment runs (v2/payments/). DEV DEFAULTS TO RAZORPAY and
    staging to Dodo, deliberately: two live rails, one per environment, so both stay
    exercised. Empty/null = the mock rail, which settles inline and moves no money. Every
    rail's KEYS live in this environment's Secrets Manager secret, never here.
  EOT
  type        = string
  default     = "razorpay"
  validation {
    condition     = contains(["", "null", "stripe", "razorpay", "dodo"], var.payment_provider)
    error_message = "payment_provider must be one of: null, stripe, razorpay, dodo (or empty for the mock rail)."
  }
}

module "stack" {
  source = "../../modules"

  providers = {
    aws           = aws
    aws.us_east_1 = aws.us_east_1
  }

  environment = "dev"
  paused      = var.paused
  # Publishing (see modules/publish.tf and deploy/PUBLISH-SERVICE.md).
  publish_image_tag        = var.publish_image_tag
  publish_admin_identities = var.publish_admin_identities

  # The builder service (modules/builder.tf) — same two-step bring-up as publish: the repo and
  # scratch bucket exist from the first apply; the Lambda exists once this tag names a pushed
  # image. redeploy.sh --only builder does the push + bump + apply as one release.
  builder_image_tag = var.builder_image_tag
  publish_engine_url       = var.publish_engine_url
  publish_engine_sha256    = var.publish_engine_sha256
  publish_engine_version   = var.publish_engine_version
  hibernate                = var.hibernate
  root_domain              = var.root_domain
  agent_hostnames          = var.agent_hostnames
  admin_hostname           = var.admin_hostname
  # dev conveniences (already the stack defaults, spelled out for contrast with prod):
  image_tag_mutability      = "MUTABLE"
  ecr_force_delete          = true
  model_proxy_desired_count = var.model_proxy_desired_count
  # Break-glass shell into the tasks (`aws ecs execute-command`) — EFS surgery and live
  # debugging. Dev only; the module default keeps it OFF everywhere that doesn't say this.
  enable_execute_command = true

  # The payment rail (v2/payments/). Razorpay TEST-mode keys live in the agentd/dev/app
  # secret; a checkout here opens Razorpay's test page and moves no real money. Return
  # origins stay unrestricted by default — dev clients run on changing origins.
  payment_provider        = var.payment_provider
  checkout_return_origins = var.checkout_return_origins

  # DEV IS STILL ON SQLITE (no DATABASE_URL in agentd/dev/app), so accounts keeps the EFS mount
  # and the single-writer rollout. Flip this the same day dev's database moves, not before —
  # dropping the volume from an environment whose data is on it detaches it from that data.
  accounts_external_database = false
  accounts_desired_count     = var.accounts_desired_count


  # TLS, DEV'S WAY: these two stay EMPTY here — dev's HTTPS comes from `root_domain` above
  # (the module manages zone + certs + wildcard, dns.tf). Staging does the inverse: it sets
  # certificate_arn/domain_name and leaves root_domain empty. Both knobs exist in both
  # environments so their SHAPE is identical; only the values pick the path.
  certificate_arn = ""
  domain_name     = ""

  # Alarms (3.5). Thresholds are deliberately loose for dev (the variables' defaults): the
  # goal here is to prove the alarms WIRE UP and can actually fire, not to tune them. The
  # money alarms (unbilled spend, ledger failures, buffer backlog, overspend) all trigger
  # at > 0 and need no tuning at any traffic level -- those are the ones that matter.
  alert_email                = var.alert_email
  cost_per_hour_alarm_usd    = var.cost_per_hour_alarm_usd
  proxy_5xx_threshold        = var.proxy_5xx_threshold
  resolve_latency_p99_ms     = var.resolve_latency_p99_ms
  login_rejection_threshold  = var.login_rejection_threshold
  # The two ABSENCE alarms stay false in dev: it has no continuous traffic, so "no sign-ins
  # for 30 minutes" is the normal state overnight.
  enable_login_absence_alarm = var.enable_login_absence_alarm
  enable_job_absence_alarm   = var.enable_job_absence_alarm

  # The clock, slowed down for dev (the variable's default). Renewals are HOURLY by default
  # and that is correct in production, where a subscription must never renew more than an
  # hour late. Dev has no subscribers and is scaled to zero for most of the day, so the
  # production cadence buys nothing and invokes a Lambda against a dead service 24 times a
  # day -- noise in the logs and in the scheduled-jobs-failing alarm.
  scheduled_job_overrides = var.scheduled_job_overrides

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
  registry_publisher_key = "Fkez25JIQDUtvwvyghwGdY2Mw//fDG7ITZCmB7CG7Nc="

  # The public marketplace. Both empty by default => its own cloudfront.net https address.
  marketplace_domain_name     = var.marketplace_domain_name
  marketplace_certificate_arn = var.marketplace_certificate_arn
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
output "region" {
  description = "This environment's AWS region (push-images.ps1, set-keys.ps1 and deploy.yml read it)."
  value       = module.stack.region
}
output "hosted_zone_name_servers" {
  description = "Route 53 nameservers for root_domain — paste these at the registrar (DOMAIN-SETUP.md step 2)."
  value       = module.stack.hosted_zone_name_servers
}

output "domain_urls" {
  description = "Every hostname the managed domain serves — open these to verify the domain end to end."
  value       = module.stack.domain_urls
}



output "platform_url" {
  description = "[platform] platform_url - THE ONE address a client bakes; everything else is discovered from it."
  value       = module.stack.platform_url
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

# ── the public marketplace (modules/marketplace.tf) ─────────────────────────────────────
#
# A CloudFront distribution over two S3 buckets, with no service behind it. deploy-marketplace.ps1
# reads these three: where to upload the page, what to invalidate, and where it ends up.

output "marketplace_url" {
  description = "The public marketplace. https on CloudFront's own certificate - no ACM cert of ours needed."
  value       = module.stack.marketplace_url
}

output "marketplace_site_bucket" {
  description = "Upload target for the built page (deploy/scripts/deploy-marketplace.ps1)."
  value       = module.stack.marketplace_site_bucket
}

output "marketplace_distribution_id" {
  description = "Distribution to invalidate after an upload. Skip it and the deploy looks like it did nothing."
  value       = module.stack.marketplace_distribution_id
}

# ── the builder service (modules/builder.tf) ────────────────────────────────────────────

output "builder_ecr_repository" {
  description = "Push the builder image here; redeploy.sh --only builder does push + tag bump + apply."
  value       = module.stack.builder_ecr_repository
}

output "builder_url" {
  description = "Where the hosted daemon sends agent window builds. Empty until builder_image_tag is set."
  value       = module.stack.builder_url
}

output "builder_scratch_bucket" {
  description = "The builds' sources-in/results-out conveyor belt (1-day expiry)."
  value       = module.stack.builder_scratch_bucket
}

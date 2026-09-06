# ─────────────────────────────────────────────────────────────────────────────
# ENVIRONMENT: staging — a root module. Same shared module as dev; only this call differs.
#
# EVERYTHING DEV HAS, STAGING HAS. Not a reduced copy: the same five services, both Lambdas,
# the registry, the public marketplace, the scheduler and every alarm. A staging environment
# that cannot do something production does is a staging environment that cannot rehearse it,
# and the things it quietly omits are exactly the things that then break in production.
#
# THE ONE THAT BITES IF YOU LEAVE IT OUT: `publish_image_tag`. The module reads
# `publish_enabled = var.publish_image_tag != ""`, so an unset tag does not fail — it silently
# creates no publish Lambda, no creator table, no KMS key, and `publish_url` comes back empty.
# Publishing then appears to be "broken in staging" when in fact it was never built.
#
# WHAT IS DELIBERATELY LOOSER THAN PRODUCTION: mutable image tags, force-deletable ECR, and
# `paused`/`hibernate` available so the whole thing can be scaled to zero between test runs.
# What is NOT looser: the alarms and the schedule, because rehearsing them is the point.
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

  # REMOTE STATE, unlike dev's local file. Staging is shared — CI deploys into it and more than
  # one person applies to it — and state on somebody's laptop means whoever has the laptop is
  # the only one who can change anything. The bucket is created by `infra/bootstrap` and is
  # named for the account, so every environment can share it under its own key.
  #
  # Commented out for the FIRST apply only: a backend cannot be initialised against a bucket
  # that does not exist yet. Run `infra/bootstrap` first, then uncomment and
  # `terraform init -migrate-state`.
  #
  # backend "s3" {
  #   bucket  = "agentd-tfstate-<account-id>"
  #   key     = "staging/terraform.tfstate"
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

module "stack" {
  source = "../../modules"

  providers = {
    aws           = aws
    aws.us_east_1 = aws.us_east_1
  }

  environment = "staging"
  paused      = var.paused
  hibernate   = var.hibernate

  # ── the domain, STAGING'S WAY: root_domain stays EMPTY. Dev hands the module the whole DNS
  # story (root_domain => module-managed zone + certs + wildcard, dns.tf); staging instead
  # rides the account's existing wildcard cert via certificate_arn below, with its one Route 53
  # record (staging.thorgodofthunder.site) created by hand in the apex zone. Same tls_enabled
  # either way (alb.tf: certificate_arn != "" || dns_managed). Both knobs exist in both
  # environments so their SHAPE is identical; only the values pick the path.
  root_domain     = var.root_domain
  agent_hostnames = var.agent_hostnames
  admin_hostname  = var.admin_hostname

  # Publishing (modules/publish.tf, deploy/PUBLISH-SERVICE.md). WITHOUT `publish_image_tag`
  # there is no publish service at all — see the header. Push the image to the repository named
  # by the `publish_ecr_repository` output, then apply again with the tag.
  publish_image_tag        = var.publish_image_tag
  publish_admin_identities = var.publish_admin_identities

  # The builder service (modules/builder.tf) — same two-step bring-up as publish: the repo and
  # scratch bucket exist from the first apply; the Lambda exists once this tag names a pushed
  # image. redeploy.sh --only builder does the push + bump + apply as one release.
  builder_image_tag = var.builder_image_tag
  publish_engine_url       = var.publish_engine_url
  publish_engine_sha256    = var.publish_engine_sha256
  publish_engine_version   = var.publish_engine_version

  # Staging conveniences. Same as dev and for the same reason: an environment that is rebuilt
  # often should not fight its own registry over tag immutability, and an ECR repository that
  # cannot be deleted outlives every teardown.
  image_tag_mutability      = "MUTABLE"
  ecr_force_delete          = true
  model_proxy_desired_count = var.model_proxy_desired_count

  # Break-glass shell into the tasks (`aws ecs execute-command`). ON here, as in dev: staging is
  # where a failure is investigated, and EFS surgery is the reason this exists. It stays OFF in
  # production, which is the module default.
  enable_execute_command = true

  # The payment rail (v2/payments/): Dodo Payments in TEST mode. EVERY Dodo value — API key,
  # webhook secret, the pay-what-you-want catalog product id, and the TEST host (Dodo splits
  # test/live by host) — lives in the agentd/staging/app secret; only the rail's NAME is
  # config here. Checkouts open Dodo's test page and move no real money. (Razorpay test keys
  # remain in the secret, unread; dev carries the razorpay flip. Production stays on the
  # module default, the mock rail.)
  payment_provider        = var.payment_provider
  checkout_return_origins = var.checkout_return_origins

  # ACCOUNTS' DATA LIVES IN POSTGRES (Neon), not in SQLite on the EFS volume — verified live
  # 2026-09-02. That lifts the two constraints the file imposed: the stop-then-start rollout
  # and the shared mount. The service becomes ordinary stateless compute, so a deploy no longer
  # has a 503 gap and more than one task can serve.
  #
  # WHICH database is still decided by DATABASE_URL in agentd/staging/app; this only tells the
  # infrastructure that the move already happened. Dev stays false until it migrates.

  # EC2 capacity for ECS (modules/ec2_capacity.tf). Building it moves NOTHING on its own —
  # every service keeps its Fargate launch type until one is explicitly given a capacity
  # provider strategy, and the ASG sits at zero instances until a task needs a machine.
  ec2_capacity_enabled = var.ec2_capacity_enabled
  ec2_instance_type    = var.ec2_instance_type
  ec2_max_instances    = var.ec2_max_instances
  # WHICH services are on EC2 — the one-at-a-time dial. Empty = everything stays on Fargate.
  ec2_services = var.ec2_services

  accounts_external_database = true
  accounts_desired_count     = var.accounts_desired_count

  # TLS comes from root_domain above — the module mints this environment's OWN certificates.
  # These two are the other path (ride a certificate that already exists, DNS by hand) and
  # stay empty here; dev keeps them empty for the same reason. They are what staging used
  # while it borrowed DEV's wildcard certificate, which is the coupling root_domain removes.
  certificate_arn = ""
  domain_name     = ""

  # Alarms. THRESHOLDS ARE PRODUCTION'S, not dev's loose ones — the whole point of staging is to
  # find out whether an alarm fires when it should and stays quiet when it should not, and an
  # alarm tuned for a dead environment answers neither question. The money alarms (unbilled
  # spend, ledger write failures, buffer backlog, overspend) trigger at > 0 and need no tuning
  # at any traffic level; those are the ones that matter.
  alert_email                = var.alert_email
  cost_per_hour_alarm_usd    = var.cost_per_hour_alarm_usd
  proxy_5xx_threshold        = var.proxy_5xx_threshold
  resolve_latency_p99_ms     = var.resolve_latency_p99_ms
  login_rejection_threshold  = var.login_rejection_threshold
  enable_login_absence_alarm = var.enable_login_absence_alarm
  enable_job_absence_alarm   = var.enable_job_absence_alarm

  # THE CLOCK, AT PRODUCTION CADENCE — deliberately unlike dev.
  #
  # Dev slows renewals to once a day because it has no subscribers and is scaled to zero for
  # most of the day, so the hourly default invokes a Lambda against a dead service 24 times a
  # day. Staging is where the hourly cadence gets rehearsed: a renewal that only ever ran at
  # midnight has never been observed racing a live `/debit`, which is precisely the interaction
  # worth seeing before production does it.
  #
  # Empty map = the module's own schedule. Add an override here only to reproduce something.
  scheduled_job_overrides = var.scheduled_job_overrides

  # THE MARKETPLACE's trust anchor: the public half of the keypair that signs index.json. It can
  # only VERIFY, never sign, which is why it lives in git rather than in Secrets Manager.
  #
  # SAME KEY AS DEV, on purpose: it must match `publisher_key` in the desktop flavors, and a
  # staging build that used a different one could not install anything the dev registry serves —
  # a mismatch is silent until someone installs, then reads as "the bundle is corrupt". Give
  # staging its own keypair only when it gets its own flavor AND its own registry contents.
  registry_publisher_key = "Fkez25JIQDUtvwvyghwGdY2Mw//fDG7ITZCmB7CG7Nc="

  # The public marketplace. Both empty => its own cloudfront.net address, over https, with no
  # certificate and no DNS of ours. Staging needs no domain to be complete.
  marketplace_domain_name     = var.marketplace_domain_name
  marketplace_certificate_arn = var.marketplace_certificate_arn
}

# ── inputs ───────────────────────────────────────────────────────────────────
#
# Values live in `staging.auto.tfvars` (gitignored) or `-var` on the command line — never here.
# An email address and a list of admins are not secrets exactly, but they are not the kind of
# thing that belongs in a public repository either.

variable "ec2_capacity_enabled" {
  description = "Build the EC2 capacity provider (launch template, ASG, instance role) so services CAN be moved off Fargate. On its own it moves nothing: no service references it and the ASG starts at zero instances."
  type        = bool
  default     = false
}

variable "ec2_services" {
  description = "Which services run on EC2 rather than Fargate — the one-at-a-time migration dial. A named service loses A-record service discovery (host networking needs SRV), so services that others discover internally move last."
  type        = list(string)
  default     = []
}

variable "ec2_instance_type" {
  description = "Size of the ECS container instances. One instance costs more than the Fargate tasks it replaces until several services share it."
  type        = string
  default     = "t3.small"
}

variable "ec2_max_instances" {
  description = "Ceiling for the container-instance ASG; the floor is always 0. 2 leaves room for a rolling deploy, which needs a second box because host networking takes the service's port on the one it occupies."
  type        = number
  default     = 2
}

variable "accounts_desired_count" {
  description = <<-EOT
    How many accounts tasks to run. Honoured only when accounts_external_database is set —
    SQLite on one file cannot have two writers.

    ONE, BY CHOICE, AND IT COSTS THE GAP-FREE DEPLOY. With a single task there is no second one
    to stay healthy, so minimum_healthy_percent 100 cannot hold and ECS stops the old task
    before starting its replacement — the brief 503 on every accounts deploy. 2 is what makes a
    deploy, an AZ failure or a task crash invisible instead, for roughly $5/month; worth
    revisiting before production carries real users.
  EOT
  type        = number
  default     = 1
}

variable "model_proxy_desired_count" {
  description = "Initial Model Proxy task count; 0 parks it without removing the service."
  type        = number
  default     = 1
}

variable "alert_email" {
  description = "Where staging alarms go. Empty = topic created, nobody subscribed — which means every alarm below fires into nothing, so set it before treating staging as a gate."
  type        = string
  default     = ""
}

variable "cost_per_hour_alarm_usd" {
  description = "Spend-rate alarm. Production's value, not dev's loose one: staging exists to find out whether this threshold is right before production has to answer that question."
  type        = number
  default     = 5
}

variable "proxy_5xx_threshold" {
  description = "Model-proxy 5xx alarm threshold, as a percentage over 5 minutes."
  type        = number
  default     = 1
}

variable "paused" {
  description = "Scale every task to 0 and disable the scheduled jobs, keeping the ALB — and therefore the URL. The cheap way to leave staging up between test runs."
  type        = bool
  default     = false
}

variable "hibernate" {
  description = "Remove the ALB, its listeners/target groups and the ECS services too. Implies paused. THE PUBLIC URL WILL CHANGE on the next apply, which means every client pointed at staging needs re-syncing."
  type        = bool
  default     = false
}

variable "builder_image_tag" {
  description = "Image tag in the builder ECR repo. Empty = no builder Lambda (agent window builds then fail on hosted, loudly)."
  type        = string
  default     = ""
}

variable "publish_image_tag" {
  description = "Image tag for the publish Lambda. EMPTY = the publish service is not created at all — no function, no creator table, no KMS key, and publish_url comes back empty. It does not fail; it silently is not there."
  type        = string
  default     = ""
}

variable "publish_admin_identities" {
  description = "Registry admins for the publish service: account ids and/or emails. Nobody can admit a creator until this is set."
  type        = list(string)
  default     = []
}

variable "publish_engine_url" {
  description = "Absolute URL of the engine installer a product stub downloads. Empty = read the registry index."
  type        = string
  default     = ""
}

variable "publish_engine_sha256" {
  description = "sha256 of that installer. Required whenever publish_engine_url is set — a stub refuses a download it cannot verify."
  type        = string
  default     = ""
}

variable "publish_engine_version" {
  description = "The engine version that installer installs, for a payload's minimum-version check."
  type        = string
  default     = ""
}

variable "scheduled_job_overrides" {
  description = "Per-job schedule overrides. Empty = the module's own cadence, which is what staging should normally run so the production clock is the thing being rehearsed."
  type        = any
  default     = {}
}

variable "marketplace_domain_name" {
  description = "Hostname for the public marketplace. Needs marketplace_certificate_arn too. Empty = CloudFront's own https address, which is enough for staging."
  type        = string
  default     = ""
}

variable "marketplace_certificate_arn" {
  description = "ACM certificate for marketplace_domain_name. MUST be in us-east-1 whatever region this deployment runs in — CloudFront reads certificates only from there."
  type        = string
  default     = ""
}

# ── the domain (../../DOMAIN-SETUP.md is the runbook) ───────────────────────────────────────
#
# ONE SUBDOMAIN PER ENVIRONMENT. Staging owns `staging.<apex>` and dev owns `dev.<apex>`, each
# with its own Route 53 zone, its own pair of ACM certificates and its own wildcard — so an
# environment's namespace is a subtree nothing else can reach into, and the two can never fight
# over a record. The bare apex is nobody's: it holds the NS delegations for those two zones and
# serves nothing.
#
# THIS REPLACED certificate_arn/domain_name BELOW, which had staging borrowing the wildcard
# certificate DEV'S terraform owns — an invisible coupling where one environment's apply could
# take the other's HTTPS down. Each environment now mints its own.
#
# A local `staging.auto.tfvars` still overrides these, and tfvars BEAT defaults — so a stale
# root_domain line there silently keeps this environment where it was. See the runbook's step 0.
variable "root_domain" {
  description = "The environment's base domain. Non-empty = the module manages Route 53 + ACM + HTTPS + the per-agent wildcard."
  type        = string
  default     = "staging.thorgodofthunder.site"
}

# The full hostname is the KEY (not a label): the module writes it into the ALB host rule and
# into the daemon's AGENTD_APP_HOSTS from this one map, so the two cannot disagree.
variable "agent_hostnames" {
  description = "Vanity hostname -> agent id (ALB host rule + AGENTD_APP_HOSTS, one map so they cannot disagree)."
  type        = map(string)
  default = {
    "platform.staging.thorgodofthunder.site" = "cloud-agent-builder"
  }
}

variable "admin_hostname" {
  description = "The standalone admin console's hostname (nginx server_name + the ALB rule that shields it from the wildcard)."
  type        = string
  default     = "admin.staging.thorgodofthunder.site"
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
  description = "Page when NO successful sign-in occurs in the window. Off until staging carries continuous traffic — it is the one alarm that treats missing data as breaching."
  type        = bool
  default     = false
}

variable "enable_job_absence_alarm" {
  description = "Page when NO scheduled job has run in 24h (a stopped billing clock). Off by default because it necessarily fires once before the first invocation; enable after the schedules have run."
  type        = bool
  default     = false
}

variable "checkout_return_origins" {
  description = "Origins /me/checkout may return a paying customer to (AGENTD_CHECKOUT_RETURN_ORIGINS). Empty = any absolute http(s) URL; set it once staging's client origins are stable, because the alternative in production is an open redirect wearing our domain."
  type        = list(string)
  default     = []
}

variable "payment_provider" {
  description = <<-EOT
    Which payment rail this environment runs (v2/payments/). STAGING DEFAULTS TO DODO and
    dev to Razorpay, deliberately: two live rails, one per environment, so both stay
    exercised. Empty/null = the mock rail, which settles inline and moves no money. Every
    rail's KEYS live in this environment's Secrets Manager secret, never here.
  EOT
  type        = string
  default     = "dodo"
  validation {
    condition     = contains(["", "null", "stripe", "razorpay", "dodo"], var.payment_provider)
    error_message = "payment_provider must be one of: null, stripe, razorpay, dodo (or empty for the mock rail)."
  }
}

# ── outputs ──────────────────────────────────────────────────────────────────
#
# THE SAME SET AS DEV, and that matters beyond symmetry: `sync-platform-urls.mjs` reads
# `platform_url`, `accounts_url`, `model_proxy_url`, `registry_url`, `ingest_url` and
# `publish_url` by name. An environment missing one of them cannot have a client pointed at it,
# and the script's failure names the output rather than the environment.

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

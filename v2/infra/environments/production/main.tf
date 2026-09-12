# ─────────────────────────────────────────────────────────────────────────────
# ENVIRONMENT: production - a root module. Same shared module as dev and staging; only this
# call differs.
#
# THIS FILE IS STAGING'S, DELIBERATELY. Production is not a hardened variant of the environment
# that works - it IS that environment, with the names changed. Every value below is the one
# staging runs today: the same five services on EC2, both Lambdas, the executor, the registry,
# the public marketplace, the scheduler, and every alarm at the same thresholds. An environment
# configured differently from the one its changes were rehearsed in has not been rehearsed.
#
# WHAT ACTUALLY DIFFERS: three names. `environment`, the state key, and the domain
# (prod.<apex> rather than staging.<apex>, which cascades into `agent_hostnames` and
# `admin_hostname`). Nothing else. If you find yourself adding a fourth difference, write down
# why here - an undocumented drift between these two files is the bug that only production sees.
#
# THE ONE THAT BITES IF YOU LEAVE IT OUT: `publish_image_tag`. The module reads
# `publish_enabled = var.publish_image_tag != ""`, so an unset tag does not fail - it silently
# creates no publish Lambda, no creator table, no KMS key, and `publish_url` comes back empty.
# Publishing then appears to be "broken in production" when in fact it was never built.
# `builder_image_tag` and `executor_image_tag` behave the same way, and the executor's absence
# is the loudest of the three: the daemon's sandbox backend falls back to "subprocess", which
# runs untrusted tools on the daemon's own box instead of in a microVM.
#
# BEFORE THE FIRST APPLY, IN THIS ORDER - the sequence is load-bearing:
#
#   1. Move state to S3. Uncomment the backend below and `terraform init -migrate-state`.
#      Production state on one laptop is one lost laptop away from nobody being able to
#      change production.
#   2. `terraform apply -target=module.stack.aws_route53_zone.main`, read
#      `hosted_zone_name_servers`, and add the NS delegation in the apex zone. ACM cannot
#      validate until that propagates, and a full apply would sit there waiting on it.
#   3. Apply with all three image tags EMPTY (ECR is created by that same apply, so the images
#      cannot exist yet). Push, then apply again with the tags set.
#   4. Merge DATABASE_URL - a Neon PRODUCTION branch, not staging's - into the
#      agentd/production/app secret BEFORE accounts serves anyone. It appears in no terraform
#      file at all: accounts reads it straight from the vault, and its absence silently means
#      SQLite on a container disk that does not survive a restart. `accounts_external_database`
#      below is set true, which asserts that this step already happened.
#   5. Real payment credentials. `payment_provider = "dodo"` with TEST values in the secret
#      takes no money, and the mock rail settles inline - either one grants credits for free
#      while the module's `require_credits` default is true.
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

  # REMOTE STATE, and here it is not a convenience. Production is shared — CI deploys into it
  # and more than one person applies to it — and state on somebody's laptop means whoever holds
  # the laptop is the only one who can change production, and losing it loses the environment. The bucket is created by `infra/bootstrap` and is
  # named for the account, so every environment can share it under its own key.
  #
  # Commented out for the FIRST apply only: a backend cannot be initialised against a bucket
  # that does not exist yet. Run `infra/bootstrap` first, then uncomment and
  # `terraform init -migrate-state`.
  #
  # backend "s3" {
  #   bucket  = "agentd-tfstate-<account-id>"
  #   key     = "production/terraform.tfstate"
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

  environment = "production"
  paused      = var.paused
  hibernate   = var.hibernate

  # ── the domain. root_domain is SET, so the module owns the whole DNS story for this
  # environment (dns.tf): its own Route 53 zone, its own pair of DNS-validated wildcard
  # certificates, HTTPS on every listener, and the wildcard host rule that gives each published
  # agent <bundle-id>.<root_domain> with no per-agent provisioning. certificate_arn/domain_name
  # below are the OTHER path - ride a certificate somebody else owns - and stay empty.
  #
  # prod.thorgodofthunder.site IS AN INTERIM NAME. Moving to the real domain is these three
  # values and nothing else in the repo (infra/DOMAIN-SETUP.md, "Changing the domain later").
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

  # The executor service (modules/executor.tf) — untrusted sandbox jobs off the daemon, one
  # microVM per call. Same two-step bring-up; bringing it up also flips the daemon's sandbox
  # backend to "microvm" (services.tf computed_env). redeploy.sh --only executor releases it
  # (the image builds FROM the daemon image, so the daemon must be pushed first).
  executor_image_tag = var.executor_image_tag
  # REFUSE TO PLAN A DAEMON THAT WOULD RUN UNTRUSTED CODE IN ITS OWN CONTAINER. Off during the
  # first bring-up of an environment, because ECR is created by that same apply and the image
  # cannot exist yet; on afterwards, so clearing the tag later fails at plan time instead of
  # silently returning the sandbox backend to "subprocess".
  require_executor       = var.require_executor
  publish_engine_url     = var.publish_engine_url
  publish_engine_sha256  = var.publish_engine_sha256
  publish_engine_version = var.publish_engine_version

  # MUTABLE TAGS, AND THIS IS NOT LAXNESS - it is what makes a second deploy possible. Every
  # service pulls `<repo>:${var.image_tag}`, which is ":latest" (services.tf), and redeploy.sh
  # pushes that same moving tag. An IMMUTABLE repository REJECTS the second push of :latest, so
  # the environment could be deployed exactly once and every release after it would fail at the
  # push. `redeploy.sh --tag <sha>` puts a permanent reference alongside it, which is what a
  # rollback should name anyway: a build, not whatever :latest points at today.
  # ── production-only hardening. The rest of this file is staging's, byte for byte; these are
  # the settings where being rebuilt-often and being real genuinely differ.
  #
  # 30-DAY SECRET RECOVERY, not staging's 0. This secret holds AGENTD_IDENTITY_KEK, which wraps
  # the token signing key at rest -- delete it with no recycle-bin hold and every stored token
  # becomes unreadable, which locks out every account at once rather than losing a credential.
  secret_recovery_window_days = 30

  # DELETION PROTECTION ON THE LOAD BALANCER, because it IS the address: every client bakes the
  # platform URL, and a replacement ALB returns on a different hostname. Staging leaves this off
  # so `hibernate` and down.ps1 keep working, which is exactly the capability production should
  # not have by accident.
  alb_deletion_protection = true

  image_tag_mutability      = "MUTABLE"
  ecr_force_delete          = true
  model_proxy_desired_count = var.model_proxy_desired_count

  # Break-glass shell into the tasks (`aws ecs execute-command`). ON, matching staging. The
  # module's default is off and the usual argument for keeping it off in production is real:
  # this is an authenticated shell into a running production task. It is on because EFS surgery
  # and a live failure investigation are things production will need at 3am, and discovering
  # then that the door is bolted is worse. Every use is logged to CloudTrail.
  enable_execute_command = true

  # The payment rail (v2/payments/): Dodo Payments. EVERY Dodo value - API key, webhook secret,
  # the pay-what-you-want catalog product id, and the host - lives in the agentd/production/app
  # secret; only the rail's NAME is config here. THE HOST IS THE TEST/LIVE SWITCH: Dodo splits
  # the two by host, so a production deployment carrying staging's DODO_API_BASE_URL opens
  # Dodo's test page and takes no money at all. (Razorpay and Stripe fields remain in the secret,
  # unread, so switching rails is a variable change and a service roll, never a schema change.)
  payment_provider        = var.payment_provider
  checkout_return_origins = var.checkout_return_origins

  # ACCOUNTS' DATA LIVES IN POSTGRES (Neon), not in SQLite on the EFS volume — verified live
  # 2026-09-02. That lifts the two constraints the file imposed: the stop-then-start rollout
  # and the shared mount. The service becomes ordinary stateless compute, so a deploy no longer
  # has a 503 gap and more than one task can serve.
  #
  # WHICH database is decided by DATABASE_URL in agentd/production/app, and NOT by this flag.
  # Set that secret field to a Neon PRODUCTION branch before accounts serves anyone: absent, the
  # service silently falls back to SQLite, and with the EFS mount gone that file lives on a
  # container disk that does not survive a restart. This line asserts the move already happened.

  # EC2 capacity for ECS (modules/ec2_capacity.tf). Building it moves NOTHING on its own —
  # every service keeps its Fargate launch type until one is explicitly given a capacity
  # provider strategy, and the ASG sits at zero instances until a task needs a machine.
  ec2_capacity_enabled = var.ec2_capacity_enabled
  ec2_capacity_pools   = var.ec2_capacity_pools
  # WHICH services are on EC2 — the one-at-a-time dial. Empty = everything stays on Fargate.
  ec2_services = var.ec2_services

  accounts_external_database = true
  accounts_desired_count     = var.accounts_desired_count

  # TLS comes from root_domain above - the module mints this environment's OWN certificates.
  # These two are the other path (ride a certificate that already exists, DNS by hand) and stay
  # empty in every environment now. They are what staging used while it borrowed dev's wildcard
  # certificate: an invisible coupling where one environment's apply could take another's HTTPS
  # down, which is exactly what root_domain removed.
  certificate_arn = ""
  domain_name     = ""

  # Alarms. These thresholds ARE production's - staging runs them unchanged precisely so that
  # whether one fires when it should, and stays quiet when it should not, is a question already
  # answered by the time this file is applied. The money alarms (unbilled spend, ledger write
  # failures, buffer backlog, overspend) trigger at > 0 and need no tuning at any traffic level;
  # those are the ones that matter. alert_email is not optional here: unset, the SNS topic is
  # created with nobody subscribed and all thirteen alarms fire into nothing.
  alert_email                = var.alert_email
  cost_per_hour_alarm_usd    = var.cost_per_hour_alarm_usd
  proxy_5xx_threshold        = var.proxy_5xx_threshold
  resolve_latency_p99_ms     = var.resolve_latency_p99_ms
  login_rejection_threshold  = var.login_rejection_threshold
  enable_login_absence_alarm = var.enable_login_absence_alarm
  enable_job_absence_alarm   = var.enable_job_absence_alarm

  # THE CLOCK. The module's own cadence, which is the production cadence - renewals hourly, so a
  # subscription that came due at 00:05 is not waiting until tomorrow for its credits.
  #
  # This is the one thing staging rehearses rather than merely mirrors: an hourly renewal racing
  # a live `/debit` is an interaction worth having seen before real money is on either side of
  # it. Dev alone slows this down, because it has no subscribers and is scaled to zero for most
  # of the day, so the hourly default would invoke a Lambda against a dead service 24 times.
  #
  # Empty map = the module's own schedule. Add an override here only to reproduce something.
  scheduled_job_overrides = var.scheduled_job_overrides

  # THE MARKETPLACE's trust anchor: the public half of the keypair that signs index.json. It can
  # only VERIFY, never sign, which is why it lives in git rather than in Secrets Manager.
  #
  # THE SAME KEY IN EVERY ENVIRONMENT, on purpose: it must match `publisher_key` in the desktop
  # flavors, and a build that used a different one could not install anything the registry
  # serves - a mismatch is silent until someone installs, then reads as "the bundle is corrupt".
  # Production earns its own keypair only when it gets its own flavor AND its own registry
  # contents, and that swap is a coordinated release, not a terraform edit.
  registry_publisher_key = "Fkez25JIQDUtvwvyghwGdY2Mw//fDG7ITZCmB7CG7Nc="

  # The public marketplace. Both empty => its own cloudfront.net address, over https, with no
  # certificate and no DNS of ours. Give it a real hostname when the storefront is something
  # customers are pointed at by name rather than by link.
  marketplace_domain_name     = var.marketplace_domain_name
  marketplace_certificate_arn = var.marketplace_certificate_arn
}

# ── inputs ───────────────────────────────────────────────────────────────────
#
# Values live in `production.auto.tfvars` (gitignored) or `-var` on the command line — never here.
# An email address and a list of admins are not secrets exactly, but they are not the kind of
# thing that belongs in a public repository either.

variable "ec2_capacity_enabled" {
  description = <<-EOT
    Build the EC2 capacity provider (launch template, ASG, instance role) so services CAN be
    moved off Fargate. On its own it moves nothing: what runs where is `ec2_services`.

    TRUE, matching staging, where the migration was done. It used to be passed as `-var` on
    every apply, and forgetting it once silently destroyed the capacity provider and dragged the
    migrated services back to Fargate - a default is what makes that impossible.
  EOT
  type        = bool
  default     = true
}

variable "ec2_services" {
  description = <<-EOT
    Which services run on EC2 rather than Fargate. The whole fleet, identical to staging as of 2026-09-05.

    Still the migration dial: SHORTEN THIS LIST to roll a service back to Fargate, which is the
    fastest way to isolate whether a problem is the service or the launch type. A named service
    loses A-record service discovery (bridge and host both need SRV), which is why every
    internal caller reads `local.accounts_internal_url` / `local.model_proxy_internal_url`
    instead of a `*.agentd.local` name — see modules/variables.tf.
  EOT
  type        = list(string)
  default     = ["web", "ingest", "daemon", "model-proxy", "accounts"]
}

variable "ec2_capacity_pools" {
  description = <<-EOT
    THE POOLS OF CONTAINER INSTANCES. Replaced ec2_instance_type + ec2_max_instances, which could
    only describe one undifferentiated pool — and one pool is what broke this environment on
    2026-09-08: five services shared two boxes with no placement strategy, ECS spread them, and
    the free memory ended up as ~1600 MiB on each box instead of ~3200 on one. The daemon wants
    a contiguous 2048. It fit in neither half, sat unplaceable, and logged nothing at all; the
    deploy just hung at "0 of 1 started".

    daemon — one box, one task. Nothing else is ever scheduled against its memory, so that
      arithmetic cannot recur. Pinned at a single instance because the daemon is pinned at a
      single task (three SQLite databases on the shared EFS mount), so a second box could hold
      nothing anyway. The task takes 3584 of the ~3900 MiB the box registers.

    shared — the four stateless services, 1280 CPU / 2560 MiB between them at one task each,
      which is 62% / 66% of one medium. Each scales its own task count inside this pool; the
      pool grows a box when the next task no longer fits. max 3 is the room to grow into, and
      costs nothing until load asks for it.

    t3.medium IS THE FLOOR for the daemon pool: a t3.small registers only ~1913 MiB, so a
    2048 MiB task would never place and the symptom is PROVISIONING forever while managed
    scaling adds more small instances that cannot help either.
  EOT
  type = map(object({
    instance_type = string
    min_size      = optional(number, 0)
    max_size      = number
    # The pool that takes the unsuffixed resource names ("<prefix>-ecs", "<prefix>-ec2").
    # At most one. It is what lets an already-running environment adopt the pool split without
    # replacing services that are not actually moving: a capacity provider cannot be renamed,
    # and changing a service's capacity_provider_strategy replaces the service.
    primary = optional(bool, false)
  }))
  default = {
    daemon = { instance_type = "t3.medium", min_size = 1, max_size = 1 }
    shared = { instance_type = "t3.medium", min_size = 1, max_size = 3, primary = true }
  }
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
  description = "Where production alarms go. Empty = topic created, nobody subscribed — which means every alarm below fires into nothing, so set it before production carries a single user."
  type        = string
  default     = ""
}

variable "cost_per_hour_alarm_usd" {
  description = "Spend-rate alarm. The real one: staging ran this same value so that whether it is right was answered somewhere cheaper than here."
  type        = number
  default     = 5
}

variable "proxy_5xx_threshold" {
  description = "Model-proxy 5xx alarm threshold, as a percentage over 5 minutes."
  type        = number
  default     = 1
}

variable "paused" {
  description = "Scale every task to 0 and disable the scheduled jobs, keeping the ALB — and therefore the URL. Inherited from staging, where it is the cheap way to idle between test runs. Here it is an outage with the lights left on."
  type        = bool
  default     = false
}

variable "hibernate" {
  description = "Remove the ALB, its listeners/target groups and the ECS services too. Implies paused. THE PUBLIC URL WILL CHANGE on the next apply, which means every client pointed at production needs re-syncing."
  type        = bool
  default     = false
}

variable "builder_image_tag" {
  description = "Image tag in the builder ECR repo. Empty = no builder Lambda (agent window builds then fail on hosted, loudly)."
  type        = string
  default     = ""
}

variable "require_executor" {
  description = <<-EOT
    Refuse to plan if the executor Lambda is absent. Without it the daemon's sandbox backend
    computes to "subprocess" and untrusted plugins, the exec tool and fenced shell commands run
    inside the daemon's own container rather than a microVM -- silently, with no error anywhere.

    FALSE ONLY UNTIL THE EXECUTOR IS RELEASED. The first apply creates ECR, so
    executor_image_tag must be empty for it and this must be off. Step 6 of the bring-up
    in the header pushes the executor image; TURN THIS ON immediately after, and before
    this environment serves anybody.
  EOT
  type        = bool
  default     = false
}

variable "executor_image_tag" {
  description = "Image tag in the executor ECR repo. Empty = no executor Lambda; the daemon's sandbox backend then stays 'subprocess' (untrusted tools isolated on-box, the pre-microvm behaviour)."
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
  description = "Per-job schedule overrides. Empty = the module's own cadence, which is what production should run - the cadence staging spent its time rehearsing."
  type        = any
  default     = {}
}

variable "marketplace_domain_name" {
  description = "Hostname for the public marketplace. Needs marketplace_certificate_arn too. Empty = CloudFront's own https address, which is enough until the storefront needs a name of its own."
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
# ONE SUBDOMAIN PER ENVIRONMENT. Production owns `prod.<apex>`, staging `staging.<apex>` and
# dev `dev.<apex>`, each with its own Route 53 zone, its own pair of ACM certificates and its
# own wildcard - so an environment's namespace is a subtree nothing else can reach into, and no
# two can fight over a record. The bare apex is nobody's: it holds the NS delegations for those
# zones and serves nothing.
#
# WHICH IS WHY PRODUCTION IS ON A SUBDOMAIN AND NOT THE APEX. The apex is deliberately dumb, and
# `prod.` is an interim home until the real domain is bought: at that point this environment
# takes the new name's apex and the three values below are the entire change.
#
# A local `production.auto.tfvars` still overrides these, and tfvars BEAT defaults - so a stale
# root_domain line there silently keeps this environment where it was. See the runbook's step 0.
variable "root_domain" {
  description = "The environment's base domain. Non-empty = the module manages Route 53 + ACM + HTTPS + the per-agent wildcard."
  type        = string
  default     = "prod.thorgodofthunder.site"
}

# The full hostname is the KEY (not a label): the module writes it into the ALB host rule and
# into the daemon's AGENTD_APP_HOSTS from this one map, so the two cannot disagree.
variable "agent_hostnames" {
  description = "Vanity hostname -> agent id (ALB host rule + AGENTD_APP_HOSTS, one map so they cannot disagree)."
  type        = map(string)
  default = {
    "platform.prod.thorgodofthunder.site" = "cloud-agent-builder"
  }
}

variable "admin_hostname" {
  description = "The standalone admin console's hostname (nginx server_name + the ALB rule that shields it from the wildcard)."
  type        = string
  default     = "admin.prod.thorgodofthunder.site"
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
  description = "Page when NO successful sign-in occurs in the window. Off until production carries continuous traffic — it is the one alarm that treats missing data as breaching."
  type        = bool
  default     = false
}

variable "enable_job_absence_alarm" {
  description = "Page when NO scheduled job has run in 24h (a stopped billing clock). Off by default because it necessarily fires once before the first invocation; enable after the schedules have run."
  type        = bool
  default     = false
}

variable "checkout_return_origins" {
  description = "Origins /me/checkout may return a paying customer to (AGENTD_CHECKOUT_RETURN_ORIGINS). Empty = any absolute http(s) URL; SET IT HERE. Production's client origins are known, and the alternative is an open redirect wearing our own domain."
  type        = list(string)
  default     = []
}

variable "payment_provider" {
  description = <<-EOT
    Which payment rail this environment runs (v2/payments/). DODO, as in staging - with one
    difference that is NOT expressed here: Dodo splits test from live by HOST, so staging's
    test host and production's live host are both just DODO_API_BASE_URL in the environment's
    own secret. A production deployment left on the test host takes real orders and settles
    none of them. Empty/null = the mock rail, which settles inline and moves no money at all -
    which, while the module's `require_credits` default is true, is a machine for handing out
    free credits on your provider keys. Every rail's KEYS live in this environment's Secrets
    Manager secret, never here.
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
# THE SAME SET AS EVERY OTHER ENVIRONMENT, and that matters beyond symmetry: `sync-platform-urls.mjs` reads
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

# ── the executor service (modules/executor.tf) ──────────────────────────────────────────

output "executor_ecr_repository" {
  description = "Push the executor image here; redeploy.sh --only executor does push + tag bump + apply."
  value       = module.stack.executor_ecr_repository
}

output "executor_url" {
  description = "Where the hosted daemon ships untrusted sandbox jobs. Empty until executor_image_tag is set."
  value       = module.stack.executor_url
}

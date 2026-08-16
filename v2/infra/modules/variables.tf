# Inputs + the services map (THE data that drives ecr.tf, alb.tf, security.tf and
# services.tf). Defaults are the dev values; override any of them via *.tfvars when a
# second environment becomes real.

variable "project" {
  description = "Product name; prefixes every resource name."
  type        = string
  default     = "agentd"
}

variable "environment" {
  description = "Environment name (dev, staging, prod)."
  type        = string
  default     = "dev"
}

variable "region" {
  description = "AWS region everything lives in."
  type        = string
  default     = "ap-northeast-1"
}

variable "image_tag" {
  description = "Which image tag the services run."
  type        = string
  default     = "latest"
}

variable "image_tag_mutability" {
  description = "MUTABLE (dev: overwrite tags) or IMMUTABLE (prod: tags are permanent)."
  type        = string
  default     = "MUTABLE"
}

variable "ecr_force_delete" {
  description = "Let `terraform destroy` remove a repo even if it still holds images (dev convenience)."
  type        = bool
  default     = true
}

variable "model_proxy_desired_count" {
  description = "Initial Model Proxy task count. Set to 0 for the one-time live service rename, push the image, then scale up."
  type        = number
  default     = 1
}

variable "paused" {
  description = <<-EOT
    THE COST SWITCH. `terraform apply -var paused=true` scales every Fargate task to 0 and
    disables the scheduled jobs; a plain `terraform apply` brings it all back. One command each
    way, and it is the same command you already run.

    WHAT IT DELIBERATELY DOES NOT TOUCH: the load balancer. That is the entire point. An ALB's
    DNS name carries an AWS-assigned suffix, so destroying it mints a NEW hostname on the way
    back up — which orphans every desktop installer already in someone's hands (their baked
    `accounts_url` stops resolving, sign-in dies, and the service looks perfectly healthy the
    whole time), and forces a web image rebuild because that URL is baked in at build time.
    Keeping the ALB costs ~$18/month and removes that entire class of problem.

    THE MATH (ap-northeast-1, 5 services at 0.25 vCPU / 0.5 GB):
      running      ~$77/mo   compute ~$56 + ALB ~$18 + alarms/secrets/storage ~$3
      paused       ~$21/mo   the ALB, the alarms, and the data. No compute.
      ALB dropped   ~$3/mo   and a new hostname every single time.
    Pausing is 73% of the saving for none of the breakage. The remaining $18 is worth spending
    until a domain exists — a Route 53 record we own is what makes dropping the ALB safe.

    Scheduled jobs are disabled while paused because they would otherwise keep firing hourly
    against a service that is not running, which pages you all night for a deliberate action.

    For the last ~$18 as well, see `hibernate`.
  EOT
  type        = bool
  default     = false
}

variable "hibernate" {
  description = <<-EOT
    PAUSE, PLUS THE LOAD BALANCER. `terraform apply -var hibernate=true` removes the ALB, its
    target groups, its listeners and the ECS services on top of everything `paused` does —
    leaving ~$3/month: the data (EFS), the secrets, the images (ECR) and the network. Implies
    `paused`, so you never have to pass both.

    THE COST IS THE HOSTNAME, and it is not recoverable by trying harder. An ALB's DNS name
    carries an AWS-assigned suffix, so the one you get back is a NEW name every time. That is
    fine for an environment with no installed users and expensive for one with them — the URL is
    baked into desktop installers and into the web image at build time, so anyone holding an old
    build has a client that resolves nothing while the service looks perfectly healthy.

    WHAT SELF-HEALS ON WAKE, so this stays a two-command routine:
      * DESKTOP flavors — `npm run dev` runs sync-platform-urls as `predev`, which reads the new
        URLs back out of Terraform. Nothing to do.
      * INTERNAL service-to-service traffic — proxy -> accounts, daemon -> proxy, the scheduled
        jobs Lambda — all use service discovery (`accounts.agentd.local`), never the ALB. A new
        ALB changes nothing for them.
      * The WEB image — needs a rebuild, because its API origins are baked in AND pinned in its
        Content-Security-Policy. The Deploy workflow reads the live ALB at build time, so
        merging to `develop` (or running Deploy manually) fixes it. That is the one extra step,
        and it is one you already do.

    Whole-service resources are removed rather than scaled, so waking is a clean create instead
    of an in-place edit of an ECS service's load-balancer attachment — which the provider may
    treat as a replacement, and an ECS service cannot be replaced create-before-destroy because
    its name must be unique in the cluster.
  EOT
  type        = bool
  default     = false
}

variable "require_credits" {
  description = <<-EOT
    MUST AN ACCOUNT HOLD CREDITS TO RUN A MODEL AT ALL? This is what makes "cloud mode costs
    credits" true rather than merely tracked.

    WHAT IT FIXES. The proxy's per-account rule is "enforce only if this account was EVER granted
    credits" (accounts `credits_enforced` = does a credit_grants row exist). That distinction
    exists so that switching enforcement on could not refuse the next message of every account
    created before credits shipped. Its side effect is a hole: an account that has never pressed
    Buy has no grant row, so a zero balance reads as "never on a credit plan" and is allowed —
    free, unlimited, on the deployment's provider keys. This closes it deployment-wide.

    WHO IT COVERS. The model proxy is the one chokepoint both desktop Cloud and hosted web cross,
    and `turn_key()` pays with the caller's own session token, so the account is identifiable on
    both. Desktop BYOK never reaches the proxy and is correctly unaffected.

    WHAT IT DOES NOT COVER. A turn with no person behind it falls back to the master key, which
    carries no account, so the gate cannot fire: cron ticks, the daemon's own system calls, and
    ANONYMOUS VISITORS TO A PUBLIC AGENT APP. That last one is a real remaining hole and needs its
    own decision (require sign-in for public apps, or accept it).

    DEFAULT TRUE, and deliberately so. The failure this direction risks is a new user being asked
    to press Buy; the failure the other direction risks is an environment quietly serving free
    unlimited inference on your provider keys. Set it false per environment to opt back out.
  EOT
  type        = bool
  default     = true
}

locals {
  # Hibernating implies paused: there is nowhere to route to, so running tasks would only burn
  # money. Written once here rather than as `var.paused || var.hibernate` in five places.
  paused = var.paused || var.hibernate
  # The routing layer exists only when not hibernating. Both are `for_each`/`count` inputs, so
  # they must be knowable at plan time — which they are, being plain variables.
  alb_services = var.hibernate ? {} : local.services
  alb_count    = var.hibernate ? 0 : 1
  # "" while hibernating. Callers (outputs, sync-platform-urls) treat an empty URL as "unknown",
  # which is exactly right: the next ALB does not have a name yet. The dimension value
  # AWS/ApplicationELB metrics are keyed by works the same way: dashboard widgets are plain JSON,
  # so an absent ALB should make them reference nothing rather than fail.
  #
  # `join("", …)` AND NOT a conditional, because these must be STRINGS even while the ALB they
  # describe is being destroyed in this very apply. The previous form —
  #
  #   try(one(aws_lb.main[*].arn_suffix), "") == null ? "" : try(one(aws_lb.main[*].arn_suffix), "")
  #
  # looks like it guarantees that and does not. Its result type is only settled by EVALUATING a
  # resource attribute, and on the hibernate apply (count 1 -> 0, ALB destroyed in the same run)
  # what landed in the dashboard body was `null`. CloudWatch then rejected the whole PutDashboard:
  #
  #   /widgets/4/properties/metrics/0/3  "Invalid metric field type, only String type is allowed"
  #
  # which failed `down.ps1 -Full` HALFWAY THROUGH — services already deleted, ALB gone, apply exit
  # 1. A cost switch that leaves the environment in pieces is worse than no cost switch.
  #
  # join() cannot do that: its type is string, statically, and an empty list joins to "" rather
  # than to null. One element in, that element out; nothing in, "" out. No try, no null path.
  alb_dns    = join("", aws_lb.main[*].dns_name)
  alb_suffix = join("", aws_lb.main[*].arn_suffix)
}

# ─────────────────────────── Alarms (see alarms.tf) ───────────────────────────

variable "alert_email" {
  description = "Where alarm notifications go. Empty = create the SNS topic but subscribe nobody (alarms still fire, into the void). AWS sends a confirmation link the recipient must click."
  type        = string
  default     = ""
}

variable "telemetry_namespace" {
  description = "CloudWatch namespace the services publish EMF metrics to. MUST match what the running tasks emit (AGENTD_TELEMETRY_NAMESPACE, default `agentd`) or every metric-math alarm silently matches nothing. See DEF-8: this is not yet per-environment."
  type        = string
  default     = "agentd"
}

variable "cost_per_hour_alarm_usd" {
  description = "Page when provider spend exceeds this many USD in one hour. Set from observed normal, not from a budget: this catches runaway loops and abuse, not overspend."
  type        = number
  default     = 5
}

variable "proxy_5xx_threshold" {
  description = "Model Proxy 5xx responses in 5 minutes before paging. An absolute count, not a percentage: at low traffic a ratio makes one error look like a 33% error rate."
  type        = number
  default     = 5
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
  description = "Page when NO successful sign-in occurs in the window. Off by default: a dev environment is legitimately silent for hours and this is the one alarm that treats missing data as breaching."
  type        = bool
  default     = false
}

variable "login_absence_window_minutes" {
  description = "Window for enable_login_absence_alarm."
  type        = number
  default     = 30
}

# ─────────────────────── Scheduled jobs (see scheduler.tf) ───────────────────────

variable "scheduled_jobs" {
  description = <<-EOT
    The clock. One entry per accounts endpoint that must run on a schedule; adding a job here
    needs no code change (the endpoint path is passed to the Lambda as data).

    CADENCE IS A COST DECISION, not a correctness one. CloudWatch bills custom metrics per
    metric per MONTH, prorated hourly and independent of datapoint count, so ledger-snapshot at
    rate(5 minutes) keeps its 11 gauges billable for all 720 hours (~$3.30/mo) while daily
    touches ~30 (~$0.14/mo). Raise it when purchase volume makes intraday resolution worth it.

    The times are STAGGERED on purpose: renewals at :00 create grants, close-expired at 00:05
    retires dead ones, and the snapshot at 00:20 therefore reports a settled balance sheet.
    Staggering also keeps two writers off SQLite's single write lock at the same minute.

    Cron is Scheduler's 6-field form (minute hour day-of-month month day-of-week year).
  EOT
  type = map(object({
    path        = string
    schedule    = string
    description = string
    enabled     = optional(bool, true)
  }))

  default = {
    subscription-renewals = {
      path        = "/subscriptions/renew-due"
      schedule    = "cron(0 * * * ? *)"
      description = "Charge every subscription whose period has ended. Hourly so a renewal is never more than an hour late; idempotent per subscription-period, so an extra run charges nobody twice."
    }
    close-expired-credits = {
      path        = "/ledger/close-expired"
      schedule    = "cron(5 0 * * ? *)"
      description = "Book breakage for credits that expired unspent. Expiry is already enforced at spend time; this is the accounting catch-up, so daily is soon enough."
    }
    ledger-snapshot = {
      path        = "/ledger/snapshot"
      schedule    = "cron(20 0 * * ? *)"
      description = "Publish the balance sheet as CloudWatch gauges (reserve, liability, creator payable, margin, cogs ratio). Daily to keep 11 custom metrics at ~$0.14/mo instead of ~$3.30."
    }
  }
}

variable "scheduled_job_overrides" {
  description = <<-EOT
    Per-environment cadence/enablement, merged onto var.scheduled_jobs BY KEY. Only the fields
    you set are overridden; everything else keeps the default above.

    WHY THIS EXISTS RATHER THAN EDITING THE DEFAULTS. Cadence is correct per ENVIRONMENT, not
    globally. Hourly subscription renewals are right in production — a renewal must never be
    more than an hour late — and pointless in a dev environment that has no subscribers and is
    scaled to zero most of the day, where the same schedule just invokes a Lambda against a
    dead service 24 times a day. Tuning dev by editing the defaults would silently retime
    production too, which is how a billing clock drifts without anyone deciding it should.

    Overriding a `schedule` does NOT relax the ordering constraint documented on
    var.scheduled_jobs: renewals must still land before close-expired, which must still land
    before the snapshot, or the snapshot reports a balance sheet mid-settlement.
  EOT
  type = map(object({
    schedule = optional(string)
    enabled  = optional(bool)
  }))
  default = {}
}

variable "scheduled_job_timezone" {
  description = "Timezone the cron expressions above are read in. UTC so the books do not shift under daylight saving."
  type        = string
  default     = "UTC"
}

variable "scheduled_job_timeout_seconds" {
  description = "Lambda timeout for one job. Must exceed a cold Fargate response plus the batch work; the handler's own HTTP timeout is set 5s below this so a slow endpoint is logged as unreachable rather than killed mid-read with no diagnostic."
  type        = number
  default     = 60
}

variable "enable_job_absence_alarm" {
  description = "Page when NO scheduled job has run in 24h (a stopped billing clock). Off by default because it necessarily fires once before the first invocation; enable after the schedules have run."
  type        = bool
  default     = false
}

variable "certificate_arn" {
  description = <<-EOT
    ACM certificate for this environment's domain. Set it and every listener becomes HTTPS
    (web moves to :443 and :80 redirects); leave it empty and nothing changes.

    WHY IT IS AN ARN AND NOT A DOMAIN. Terraform can request an ACM cert, but DNS-validating one
    it cannot create records for makes `apply` HANG until someone adds a CNAME by hand — a
    twenty-minute wait that ends in a timeout and a half-applied environment. Issuing the cert is
    a two-click job in the ACM console (or one `aws acm request-certificate` plus the CNAME), and
    handing the finished ARN here keeps apply deterministic.

    UNTIL THIS IS SET, SESSION TOKENS CROSS THE INTERNET IN CLEARTEXT. That is survivable while
    the only user is you and unacceptable the moment anyone else signs in.

    NOT AUTOMATIC: .github/workflows/deploy.yml bakes the client's API origins into the web image
    and builds them from the ALB's hostname over http/ws. Turning TLS on here without updating
    that step ships a page served over https whose first request is http — which the browser
    blocks as mixed content, and which reads as "the backend is down". Set `domain_name` too and
    change deploy.yml's build args in the SAME commit.
  EOT
  type        = string
  default     = ""

  validation {
    condition     = var.certificate_arn == "" || can(regex("^arn:aws:acm:", var.certificate_arn))
    error_message = "certificate_arn must be an ACM certificate ARN (arn:aws:acm:…), or empty."
  }
}

variable "domain_name" {
  description = "The hostname clients use (must match certificate_arn's cert). Only shapes the URLs in outputs — DNS itself is yours to point at the ALB. Empty = use the ALB's own hostname."
  type        = string
  default     = ""
}

# ── the public marketplace (marketplace.tf) ─────────────────────────────────────────────
#
# All three are optional. With none of them set the marketplace is live on its distribution's own
# *.cloudfront.net address, over https, with no certificate and no domain — which is the whole
# point of putting it on CloudFront rather than behind the ALB.

variable "marketplace_domain_name" {
  description = <<-EOT
    Hostname for the public marketplace (e.g. "agents.example.com"). Needs
    `marketplace_certificate_arn` as well — an alias without a certificate that covers it is
    rejected by CloudFront, so the two only take effect together. DNS is yours to point at the
    distribution (an ALIAS/CNAME record to its domain name, in `marketplace_url`).

    Empty = the distribution's own *.cloudfront.net name, which already serves https.
  EOT
  type        = string
  default     = ""
}

variable "marketplace_certificate_arn" {
  description = <<-EOT
    ACM certificate for `marketplace_domain_name`.

    MUST BE IN us-east-1, whatever region the rest of this deployment runs in — CloudFront reads
    certificates only from there. This is NOT the same certificate as `certificate_arn` (that one
    is regional, for the ALB), and pasting the regional ARN here fails the apply with a message
    that does not mention regions.
  EOT
  type        = string
  default     = ""

  validation {
    condition = (
      var.marketplace_certificate_arn == "" ||
      can(regex("^arn:aws:acm:us-east-1:", var.marketplace_certificate_arn))
    )
    error_message = "marketplace_certificate_arn must be an ACM certificate in us-east-1 (CloudFront reads them from nowhere else), or empty."
  }
}

variable "marketplace_price_class" {
  description = <<-EOT
    Which edge locations serve the marketplace. PriceClass_200 by default: PriceClass_100 is
    cheaper but covers only North America and Europe, and this deployment's own users are in Asia,
    so the "cheap" option is the slow one for the people most likely to be looking. PriceClass_All
    adds South America, Australia and India.

    Free-tier traffic makes the difference between these effectively zero; it becomes a real
    number only once the store is busy.
  EOT
  type        = string
  default     = "PriceClass_200"

  validation {
    condition     = contains(["PriceClass_100", "PriceClass_200", "PriceClass_All"], var.marketplace_price_class)
    error_message = "marketplace_price_class must be PriceClass_100, PriceClass_200 or PriceClass_All."
  }
}

variable "registry_publisher_key" {
  description = "Base64 ed25519 PUBLIC key that signed the marketplace index — the hosted daemon pins downloads to it. This is the public half; it is safe in state, in task env, and in git (the desktop flavors already carry the same string). Empty = unsigned mode: sha256 is still checked but a rewritten index.json would be accepted, so leave it empty only for a private registry you fully control."
  type        = string
  default     = ""

  validation {
    # A base64 raw ed25519 public key is 32 bytes => 44 chars with padding. Catching a truncated
    # paste here matters because the failure is otherwise invisible until an install: the daemon
    # boots fine, the store lists fine, and only the download rejects — reading as "the bundle is
    # broken" rather than "the key is wrong".
    condition     = var.registry_publisher_key == "" || can(regex("^[A-Za-z0-9+/]{43}=$", var.registry_publisher_key))
    error_message = "registry_publisher_key must be a base64 ed25519 public key (44 chars ending in '='), or empty. Use the public half printed by `agentd bundle keygen` — never the private key."
  }
}

# ─────────────────────────── The services map ───────────────────────────
# ONE entry per container. Adding a service here gives it an ECR repo, ALB target
# group + listener + firewall holes, service discovery, and a Fargate service — no
# other file changes. secret_keys maps a container env var -> the JSON key inside
# the app secret (data.tf); efs mounts the shared /data access point.

variable "services" {
  type = map(object({
    port        = number
    health_path = string
    env         = optional(map(string), {})
    secret_keys = optional(map(string), {})
    efs         = optional(bool, false)
    # SINGLE-WRITER: this service owns a datastore that exactly one task may write at a time
    # (accounts, on SQLite over EFS). It forces a stop-then-start rollout instead of the default
    # start-then-stop, because the overlap window puts TWO writers on one file and the second one
    # cannot open it: "sqlite3.OperationalError: database is locked", at startup, every deploy.
    # The cost is a few seconds of downtime for that service, which a desired_count=1 service
    # already has. Remove it when accounts moves to Postgres.
    single_writer = optional(bool, false)
    cpu           = optional(number, 256) # Fargate CPU units (256 = 0.25 vCPU)
    memory        = optional(number, 512) # MB
    desired_count = optional(number, 1)
    # Seconds a replaced task keeps draining. Short by default so a rollout isn't held
    # open by idle connections; raise it for services with long-lived ones (see alb.tf).
    deregistration_delay = optional(number, 30)
    # Seconds after a task starts during which ALB health-check failures do NOT kill it.
    # Covers cold start (image pull is already excluded, but interpreter + app boot is not).
    health_check_grace = optional(number, 60)
  }))

  default = {
    # web — the static UI (nginx). API URLs are baked into the image at BUILD time,
    # so this image must be built with the ALB hostname (see push-images.ps1).
    web = {
      port        = 80
      health_path = "/"
    }

    # model-proxy — LiteLLM proxy. PUBLIC (platform-keys mode): signed-in desktop
    # daemons call it with their accounts session token; custom_auth.py resolves the
    # token via the accounts service, and the usage callback writes each call's cost
    # to the ledger. The cloud daemon still reaches it internally
    # (model-proxy.agentd.local) with the master key. The liveness path is
    # unauthenticated on the pinned litellm (1.88.1).
    "model-proxy" = {
      port        = 4000
      health_path = "/health/liveliness"
      env = {
        ACCOUNTS_URL = "http://accounts.agentd.local:4100"
      }
      secret_keys = {
        LITELLM_MASTER_KEY    = "LITELLM_MASTER_KEY"
        ACCOUNTS_INTERNAL_KEY = "ACCOUNTS_INTERNAL_KEY"
        GEMINI_API_KEY        = "GEMINI_API_KEY"
        DEEPSEEK_API_KEY      = "DEEPSEEK_API_KEY"
        MOONSHOT_API_KEY      = "MOONSHOT_API_KEY"
        OPENAI_API_KEY        = "OPENAI_API_KEY"
      }
    }

    # accounts — sign-in / metering. Uses SQLite on EFS for now (RDS is a follow-up
    # that lands together with the app's Postgres support).
    accounts = {
      port        = 4100
      health_path = "/health"
      env = {
        AGENTD_ACCOUNTS_DB = "/data/accounts.db"
        # public-exposure hardening (see accounts/app.py header for the contract).
        # ACCOUNTS_SESSION_TTL_DAYS is GONE: credentials are signed tokens that carry their own
        # expiry now, so there is no server-side session row whose lifetime this could set.
        # Access-token life is AGENTD_AUTH_ACCESS_TTL_S; refresh life is AGENTD_AUTH_REFRESH_*.
        ACCOUNTS_RATE_LIMIT = "10/60"
        # CORS: the web client's origin. "*" until the web origin is stable (browser
        # clients only; the desktop app and the Model Proxy are not subject to CORS).
        ACCOUNTS_CORS_ORIGINS = "*"
      }
      single_writer = true
      secret_keys = {
        ACCOUNTS_INTERNAL_KEY = "ACCOUNTS_INTERNAL_KEY"
        # Wraps the token signing key at rest (identity/infrastructure/sqlite_key_store.py).
        # Absent, keys are stored in clear and the service logs a warning on first use.
        AGENTD_IDENTITY_KEK = "AGENTD_IDENTITY_KEK"
      }
      efs = true
    }

    # ingest — the mail slot for telemetry from machines we do not own: the desktop
    # daemon (which runs on the USER's PC, so its stdout never reaches CloudWatch) and
    # the browser client. Validates against an allowlist and prints EMF; no database,
    # no downstream, no state. The only service in the metering plan that had to exist.
    #
    # ACCOUNTS_URL is optional here on purpose: unauthenticated events are ACCEPTED
    # (anonymous), because "the daemon failed to start" is the most valuable event on
    # this endpoint and a client that cannot start often cannot sign in either.
    ingest = {
      port        = 4200
      health_path = "/health"
      env = {
        ACCOUNTS_URL = "http://accounts.agentd.local:4100"
        # Generous per IP: one desktop batches every ~30s, and a machine in a reboot
        # loop is exactly what we want to SEE rather than throttle into silence.
        INGEST_RATE_LIMIT   = "60/60"
        INGEST_CORS_ORIGINS = "*"
      }
    }

    # daemon — the agent engine + WebSocket. Mounts EFS for per-user state; reaches
    # model-proxy/accounts by their service-discovery names. A plain HTTP GET to the WS
    # root returns 426 Upgrade Required; /healthz exists specifically for the ALB.
    daemon = {
      port        = 8787
      health_path = "/healthz"
      env = {
        AGENTD_HOST            = "0.0.0.0"
        AGENTD_PORT            = "8787"
        AGENTD_HOME            = "/data"
        AGENTD_STATE_DIR       = "/data/state"
        AGENTD_WORKSPACE       = "/data/workspace"
        AGENTD_ACCOUNTS_URL    = "http://accounts.agentd.local:4100"
        AGENTD_MODEL_PROXY_URL = "http://model-proxy.agentd.local:4000"
        # UNTRUSTED plugin tier -> a child process per tool call. This daemon serves many people
        # off one filesystem, and a marketplace agent's own plugins (agents/<id>/plugins/) are
        # code a stranger wrote. In-process, that code holds everything this task holds: the
        # provider keys in its environment, the credential vault handle, and every other account's
        # directory on the shared EFS mount.
        #
        # BOTH are needed, and this is the trap: AGENTD_SANDBOX_PLUGINS alone routes untrusted
        # tools through the sandbox SEAM, whose default backend on a daemon that has not set
        # AGENTD_MULTI_TENANT is the in-process passthrough — the boundary would be wired up and
        # enforcing nothing, with no error to say so. Set explicitly rather than relying on the
        # multi_tenant default, because this deployment does per-account isolation through the
        # accounts/user_state overlay and does not flip that flag.
        AGENTD_SANDBOX_PLUGINS = "1"
        AGENTD_SANDBOX_BACKEND = "subprocess"
      }
      # WebSocket sessions are long-lived, so a replaced daemon gets a real drain window
      # (a 30s cut-off would drop live conversations mid-turn), and a longer boot grace
      # because it loads the whole agent runtime before /healthz answers.
      deregistration_delay = 120
      health_check_grace   = 120
      secret_keys = {
        AGENTD_MODEL_PROXY_KEY = "LITELLM_MASTER_KEY"
        # MCP servers inherit the daemon's environment (mcp/session.py:resolve_subprocess), so
        # a server's credentials are just task env — workspace-mcp reads these two itself.
        GOOGLE_OAUTH_CLIENT_ID     = "GOOGLE_OAUTH_CLIENT_ID"
        GOOGLE_OAUTH_CLIENT_SECRET = "GOOGLE_OAUTH_CLIENT_SECRET"
      }
      efs = true
    }
  }
}

locals {
  services = merge(
    var.services,
    {
      "model-proxy" = merge(
        var.services["model-proxy"],
        { desired_count = var.model_proxy_desired_count }
      )
    }
  )
  name_prefix = "${var.project}-${var.environment}"
  common_tags = {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

# ── the PUBLISH SERVICE (see publish.tf) ────────────────────────────────────────────────

variable "publish_image_tag" {
  description = <<-EOT
    Image tag for the publish Lambda, e.g. "v1" or a git sha. EMPTY (the default) creates only the
    ECR repo, the tables and the key — not the function.

    That two-step is deliberate, not a limitation: an image-based Lambda cannot be created before
    its image exists, so a single apply would fail halfway with the repo created and the function
    not. Apply once, push an image, set this, apply again.
  EOT
  type        = string
  default     = ""
}

variable "publish_listener_port" {
  description = "ALB listener port for POST /registry/publish. Its own port, matching how every other service here is exposed; a path rule on the web listener is the natural move once a domain lands."
  type        = number
  default     = 4300
}

variable "publish_timeout_seconds" {
  description = "Lambda timeout. It compiles a Windows installer and uploads two artifacts; the index lease is set to twice this, so an overrun can never let two publishes into the index at once."
  type        = number
  default     = 300
}

variable "publish_memory_mb" {
  description = "Lambda memory. makensis is CPU-bound and Lambda scales CPU with memory, so this is really a speed dial."
  type        = number
  default     = 2048
}

# The ENGINE a published stub installs. Normally all three stay EMPTY and the service reads the
# registry's own `engine` block — publish an engine once and every stub built afterwards follows it
# with no redeploy. Set them only to point at an engine that is not in the registry yet.
variable "publish_engine_url" {
  description = "Override: absolute URL of the engine installer a stub should download. Empty => read from the registry index."
  type        = string
  default     = ""
}

variable "publish_engine_sha256" {
  description = "Override: sha256 of that installer. A stub REFUSES to run a download it cannot verify, so this is required whenever publish_engine_url is set."
  type        = string
  default     = ""
}

variable "publish_engine_version" {
  description = "Override: the engine version that installer installs (used for a payload's minimum-version check)."
  type        = string
  default     = ""
}

variable "publish_admin_identities" {
  description = "Who may call the publish service's admin endpoints (roster admit/revoke/pending): account ids and/or emails, matched case-insensitively. EMPTY = the admin door is closed to everyone (fail-closed), which is the right state for a deployment that has not decided its admins yet."
  type        = list(string)
  default     = []
}

# Operator break-glass: `aws ecs execute-command` (an SSM shell) into the running tasks — the one
# supported way to reach the EFS from outside the VPC (tenant-data surgery, live debugging).
# Default OFF: an interactive shell inside the containers is not part of normal operation, and
# production should have to say otherwise explicitly. Running tasks only gain the exec agent on
# their next deployment, so flipping this needs a service bounce to take effect.
variable "enable_execute_command" {
  description = "Allow `aws ecs execute-command` (SSM shell) into the Fargate tasks. Dev convenience / break-glass; keep false in production."
  type        = bool
  default     = false
}

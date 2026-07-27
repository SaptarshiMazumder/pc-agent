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

# ─────────────────────────── The services map ───────────────────────────
# ONE entry per container. Adding a service here gives it an ECR repo, ALB target
# group + listener + firewall holes, service discovery, and a Fargate service — no
# other file changes. secret_keys maps a container env var -> the JSON key inside
# the app secret (data.tf); efs mounts the shared /data access point.

variable "services" {
  type = map(object({
    port          = number
    health_path   = string
    env           = optional(map(string), {})
    secret_keys   = optional(map(string), {})
    efs           = optional(bool, false)
    cpu           = optional(number, 256) # Fargate CPU units (256 = 0.25 vCPU)
    memory        = optional(number, 512) # MB
    desired_count = optional(number, 1)
  }))

  default = {
    # web — the static UI (nginx). API URLs are baked into the image at BUILD time,
    # so this image must be built with the ALB hostname (see push-images.ps1).
    web = {
      port        = 80
      health_path = "/"
    }

    # gateway — LiteLLM model proxy. PUBLIC (platform-keys mode): signed-in desktop
    # daemons call it with their accounts session token; custom_auth.py resolves the
    # token via the accounts service, and the usage callback writes each call's cost
    # to the ledger. The cloud daemon still reaches it internally
    # (gateway.agentd.local) with the master key. The liveness path is
    # unauthenticated on the pinned litellm (1.88.1).
    gateway = {
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
      }
    }

    # accounts — sign-in / metering. Uses SQLite on EFS for now (RDS is a follow-up
    # that lands together with the app's Postgres support).
    accounts = {
      port        = 4100
      health_path = "/health"
      env = {
        AGENTD_ACCOUNTS_DB = "/data/accounts.db"
        # public-exposure hardening (see deploy/accounts/app.py header for the contract)
        ACCOUNTS_SESSION_TTL_DAYS = "30"
        ACCOUNTS_RATE_LIMIT       = "10/60"
        # CORS: the web client's origin. "*" until the web origin is stable (browser
        # clients only; the desktop app and the model gateway are not subject to CORS).
        ACCOUNTS_CORS_ORIGINS = "*"
      }
      secret_keys = {
        ACCOUNTS_INTERNAL_KEY = "ACCOUNTS_INTERNAL_KEY"
      }
      efs = true
    }

    # daemon — the agent engine + WebSocket. Mounts EFS for per-user state; reaches
    # gateway/accounts by their service-discovery names. A plain HTTP GET to the WS
    # root returns 426 Upgrade Required; /healthz exists specifically for the ALB.
    daemon = {
      port        = 8787
      health_path = "/healthz"
      env = {
        AGENTD_HOST              = "0.0.0.0"
        AGENTD_PORT              = "8787"
        AGENTD_HOME              = "/data"
        AGENTD_STATE_DIR         = "/data/state"
        AGENTD_WORKSPACE         = "/data/workspace"
        AGENTD_ACCOUNTS_URL      = "http://accounts.agentd.local:4100"
        AGENTD_MODEL_GATEWAY_URL = "http://gateway.agentd.local:4000"
      }
      secret_keys = {
        AGENTD_MODEL_GATEWAY_KEY = "LITELLM_MASTER_KEY"
      }
      efs = true
    }
  }
}

locals {
  name_prefix = "${var.project}-${var.environment}"
  common_tags = {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

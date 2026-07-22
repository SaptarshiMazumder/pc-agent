# ─────────────────────────────────────────────────────────────────────────────
# ENVIRONMENT: dev — the RECIPE. Composes the reusable modules (network, security, iam,
# ecr, cluster, data, alb) and runs the 4 containers (service module, called 4×).
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
  }
}

provider "aws" {
  region = "ap-northeast-1"
}

locals {
  project     = "agentd"
  environment = "dev"
  region      = "ap-northeast-1"
  image_tag   = "latest" # which image tag the services run
}

# ─────────────────────────── Foundation (Phase 1) ───────────────────────────

module "network" {
  source      = "../../modules/network"
  project     = local.project
  environment = local.environment
}

module "security" {
  source      = "../../modules/security"
  project     = local.project
  environment = local.environment
  vpc_id      = module.network.vpc_id
}

module "iam" {
  source      = "../../modules/iam"
  project     = local.project
  environment = local.environment
}

module "ecr" {
  source       = "../../modules/ecr"
  project      = local.project
  environment  = local.environment
  force_delete = true
}

module "cluster" {
  source      = "../../modules/cluster"
  project     = local.project
  environment = local.environment
  vpc_id      = module.network.vpc_id
}

# ─────────────────────────── Data + edge (Phase 2 & 3) ───────────────────────────

module "data" {
  source      = "../../modules/data"
  project     = local.project
  environment = local.environment
  subnet_ids  = module.network.public_subnet_ids
  efs_sg_id   = module.security.efs_sg_id
}

module "alb" {
  source      = "../../modules/alb"
  project     = local.project
  environment = local.environment
  vpc_id      = module.network.vpc_id
  subnet_ids  = module.network.public_subnet_ids
  alb_sg_id   = module.security.alb_sg_id
}

# ─────────────────────────── The 4 containers (Phase 4) ───────────────────────────
# A small local so we don't repeat the shared infra references in every service call.
locals {
  svc_shared = {
    project            = local.project
    environment        = local.environment
    cluster_arn        = module.cluster.cluster_arn
    subnet_ids         = module.network.public_subnet_ids
    security_group_id  = module.security.service_sg_id
    execution_role_arn = module.iam.execution_role_arn
    task_role_arn      = module.iam.task_role_arn
    log_group_name     = module.cluster.log_group_name
    region             = local.region
    namespace_id       = module.cluster.namespace_id
  }
}

# gateway — LiteLLM model proxy. INTERNAL only (reached by the daemon via gateway.agentd.local).
module "svc_gateway" {
  source = "../../modules/service"

  name           = "gateway"
  image          = "${module.ecr.repository_urls["gateway"]}:${local.image_tag}"
  container_port = 4000

  secrets = {
    LITELLM_MASTER_KEY = "${module.data.app_secret_arn}:LITELLM_MASTER_KEY::"
    GEMINI_API_KEY     = "${module.data.app_secret_arn}:GEMINI_API_KEY::"
    DEEPSEEK_API_KEY   = "${module.data.app_secret_arn}:DEEPSEEK_API_KEY::"
  }

  project            = local.svc_shared.project
  environment        = local.svc_shared.environment
  cluster_arn        = local.svc_shared.cluster_arn
  subnet_ids         = local.svc_shared.subnet_ids
  security_group_id  = local.svc_shared.security_group_id
  execution_role_arn = local.svc_shared.execution_role_arn
  task_role_arn      = local.svc_shared.task_role_arn
  log_group_name     = local.svc_shared.log_group_name
  region             = local.svc_shared.region
  namespace_id       = local.svc_shared.namespace_id
}

# accounts — sign-in / metering. EXPOSED. Uses SQLite on EFS for now (RDS is provisioned and
# ready, but wiring accounts to Postgres needs an app change — a follow-up).
module "svc_accounts" {
  source = "../../modules/service"

  name           = "accounts"
  image          = "${module.ecr.repository_urls["accounts"]}:${local.image_tag}"
  container_port = 4100

  environment_vars = {
    AGENTD_ACCOUNTS_DB = "/data/accounts.db"
  }

  exposed          = true
  target_group_arn = module.alb.target_group_arns["accounts"]

  efs_id              = module.data.efs_id
  efs_access_point_id = module.data.efs_access_point_id
  mount_path          = "/data"

  project            = local.svc_shared.project
  environment        = local.svc_shared.environment
  cluster_arn        = local.svc_shared.cluster_arn
  subnet_ids         = local.svc_shared.subnet_ids
  security_group_id  = local.svc_shared.security_group_id
  execution_role_arn = local.svc_shared.execution_role_arn
  task_role_arn      = local.svc_shared.task_role_arn
  log_group_name     = local.svc_shared.log_group_name
  region             = local.svc_shared.region
  namespace_id       = local.svc_shared.namespace_id
}

# daemon — the agent engine + WebSocket. EXPOSED. Mounts EFS for per-user state; reaches
# gateway/accounts by their service-discovery names.
module "svc_daemon" {
  source = "../../modules/service"

  name           = "daemon"
  image          = "${module.ecr.repository_urls["daemon"]}:${local.image_tag}"
  container_port = 8787

  environment_vars = {
    AGENTD_HOST              = "0.0.0.0"
    AGENTD_PORT              = "8787"
    AGENTD_HOME              = "/data"
    AGENTD_STATE_DIR         = "/data/state"
    AGENTD_WORKSPACE         = "/data/workspace"
    AGENTD_ACCOUNTS_URL      = "http://accounts.agentd.local:4100"
    AGENTD_MODEL_GATEWAY_URL = "http://gateway.agentd.local:4000"
  }
  secrets = {
    AGENTD_MODEL_GATEWAY_KEY = "${module.data.app_secret_arn}:LITELLM_MASTER_KEY::"
  }

  exposed          = true
  target_group_arn = module.alb.target_group_arns["daemon"]

  efs_id              = module.data.efs_id
  efs_access_point_id = module.data.efs_access_point_id
  mount_path          = "/data"

  project            = local.svc_shared.project
  environment        = local.svc_shared.environment
  cluster_arn        = local.svc_shared.cluster_arn
  subnet_ids         = local.svc_shared.subnet_ids
  security_group_id  = local.svc_shared.security_group_id
  execution_role_arn = local.svc_shared.execution_role_arn
  task_role_arn      = local.svc_shared.task_role_arn
  log_group_name     = local.svc_shared.log_group_name
  region             = local.svc_shared.region
  namespace_id       = local.svc_shared.namespace_id
}

# web — the static UI (nginx). EXPOSED. API URLs are baked into the image at BUILD time,
# so this image must be built with the ALB hostname (see the Phase 4 runbook).
module "svc_web" {
  source = "../../modules/service"

  name           = "web"
  image          = "${module.ecr.repository_urls["web"]}:${local.image_tag}"
  container_port = 80

  exposed          = true
  target_group_arn = module.alb.target_group_arns["web"]

  project            = local.svc_shared.project
  environment        = local.svc_shared.environment
  cluster_arn        = local.svc_shared.cluster_arn
  subnet_ids         = local.svc_shared.subnet_ids
  security_group_id  = local.svc_shared.security_group_id
  execution_role_arn = local.svc_shared.execution_role_arn
  task_role_arn      = local.svc_shared.task_role_arn
  log_group_name     = local.svc_shared.log_group_name
  region             = local.svc_shared.region
  namespace_id       = local.svc_shared.namespace_id
}

# ─────────────────────────── Outputs ───────────────────────────

# The gateway repo pre-existed the module refactor; re-track it in place, don't recreate.
moved {
  from = module.gateway_ecr.aws_ecr_repository.this
  to   = module.ecr.aws_ecr_repository.this["gateway"]
}

output "gateway_repo_url" {
  description = "Where to push the gateway image."
  value       = module.ecr.repository_urls["gateway"]
}

output "repository_urls" {
  description = "All 4 image push targets."
  value       = module.ecr.repository_urls
}

output "app_url" {
  description = "The public URL of the app (serves once the containers are healthy)."
  value       = "http://${module.alb.alb_dns_name}"
}

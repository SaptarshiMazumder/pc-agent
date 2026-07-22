# ─────────────────────────────────────────────────────────────────────────────
# ENVIRONMENT: staging — the SAME recipe as dev, one word changed (environment). Not applied
# yet (we go live in dev first).
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
  environment = "staging"
}

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
  source      = "../../modules/ecr"
  project     = local.project
  environment = local.environment
}

module "cluster" {
  source      = "../../modules/cluster"
  project     = local.project
  environment = local.environment
  vpc_id      = module.network.vpc_id
}

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

output "gateway_repo_url" {
  description = "Where to push the gateway image."
  value       = module.ecr.repository_urls["gateway"]
}

output "app_url" {
  description = "The public URL of the app (serves traffic once Phase 4 runs the containers)."
  value       = "http://${module.alb.alb_dns_name}"
}

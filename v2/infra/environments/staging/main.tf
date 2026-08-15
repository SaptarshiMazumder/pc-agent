# ─────────────────────────────────────────────────────────────────────────────
# ENVIRONMENT: staging — a root module. Same shared module as dev; only this call differs.
# Not applied yet — running `terraform init && terraform apply` here builds a
# complete, isolated agentd-staging environment from scratch.
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

module "stack" {
  source = "../../modules"

  environment = "staging"
  # same values as dev for now; tighten (IMMUTABLE tags, no force-delete) when
  # staging becomes a real gate in front of prod.
  image_tag_mutability = "MUTABLE"
  ecr_force_delete     = true
}

output "repository_urls" {
  value = module.stack.repository_urls
}

output "app_url" {
  value = module.stack.app_url
}

output "platform_url" {
  description = "[platform] platform_url - THE ONE address a client bakes; everything else is discovered from it."
  value       = module.stack.platform_url
}

output "accounts_url" {
  value = module.stack.accounts_url
}

output "model_proxy_url" {
  value = module.stack.model_proxy_url
}

# Deprecated compatibility alias.
output "model_gateway_url" {
  value = module.stack.model_proxy_url
}

output "registry_url" {
  value = module.stack.registry_url
}

output "registry_bucket" {
  value = module.stack.registry_bucket
}

output "marketplace_url" {
  value = module.stack.marketplace_url
}

output "marketplace_site_bucket" {
  value = module.stack.marketplace_site_bucket
}

output "marketplace_distribution_id" {
  value = module.stack.marketplace_distribution_id
}

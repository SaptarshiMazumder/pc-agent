# ─────────────────────────────────────────────────────────────────────────────
# ENVIRONMENT: prod — a root module. Same shared module; production values on the call.
# Not applied yet. Before the first real prod apply, also move state to the S3
# backend (../../bootstrap created the bucket) — prod state should not live on
# one laptop.
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

  environment = "production"
  # prod hardening: image tags are permanent, and destroy can never eat images.
  image_tag_mutability = "IMMUTABLE"
  ecr_force_delete     = false
}

output "repository_urls" {
  value = module.stack.repository_urls
}

output "app_url" {
  value = module.stack.app_url
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

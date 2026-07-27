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

output "accounts_url" {
  value = module.stack.accounts_url
}

output "model_gateway_url" {
  value = module.stack.model_gateway_url
}

output "registry_url" {
  value = module.stack.registry_url
}

output "registry_bucket" {
  value = module.stack.registry_bucket
}

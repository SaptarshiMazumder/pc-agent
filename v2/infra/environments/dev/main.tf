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
  }
}

provider "aws" {
  region = "ap-northeast-1"
}

module "stack" {
  source = "../../modules"

  environment = "dev"
  # dev conveniences (already the stack defaults, spelled out for contrast with prod):
  image_tag_mutability = "MUTABLE"
  ecr_force_delete     = true
}

# ── Pass-through outputs (push-images.ps1 and the desktop flavors read these) ──

output "repository_urls" {
  description = "All image push targets."
  value       = module.stack.repository_urls
}

output "gateway_repo_url" {
  description = "Where to push the gateway image."
  value       = module.stack.gateway_repo_url
}

output "app_url" {
  description = "The public URL of the app."
  value       = module.stack.app_url
}

output "accounts_url" {
  description = "[platform] accounts_url for the desktop flavors."
  value       = module.stack.accounts_url
}

output "model_gateway_url" {
  description = "[platform] model_gateway_url for the desktop flavors."
  value       = module.stack.model_gateway_url
}

output "registry_url" {
  description = "[store] registry_url for the desktop flavors."
  value       = module.stack.registry_url
}

output "registry_bucket" {
  description = "Upload target for deploy/registry/publish.py."
  value       = module.stack.registry_bucket
}

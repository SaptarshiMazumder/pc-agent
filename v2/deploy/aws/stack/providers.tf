# ─────────────────────────────────────────────────────────────────────────────
# THE STACK — the whole agentd cloud environment as one flat Terraform root.
# One directory, one state. Each concern lives in its own FILE (network.tf,
# security.tf, …) and every reference is a direct resource reference — no
# module plumbing. What runs is data: the `services` map in variables.tf.
#
# Sibling roots (separate lifecycles, applied rarely, with admin credentials):
#   ../bootstrap     — the S3 state bucket
#   ../github-oidc   — the CI deploy role
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
  region = var.region
}

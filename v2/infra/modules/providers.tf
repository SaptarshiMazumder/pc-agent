# ─────────────────────────────────────────────────────────────────────────────
# modules/ — the whole agentd cloud environment, flat: every concern in its own
# FILE (network.tf, security.tf, …), every reference a direct resource reference.
# What runs is data: the `services` map in variables.tf.
#
# This directory is ONE shared child module — parameterized resource declarations,
# not applied directly. Each folder under ../environments/ (dev, staging, prod)
# is a root module that instantiates it (as module "stack") with its own variable
# values and its own state file. Run terraform THERE, not here.
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
    # Zips the scheduled-jobs Lambda source at plan time (scheduler.tf), so there is no
    # build step and no committed artefact.
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }
}

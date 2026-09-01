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
      # A SECOND aws provider, pinned to us-east-1, because CloudFront reads ACM certificates
      # from that region and nowhere else (dns.tf mints one there). Every environment root must
      # now pass it on the module call:
      #
      #   provider "aws" { alias = "us_east_1"  region = "us-east-1" }
      #   module "stack" { providers = { aws = aws, aws.us_east_1 = aws.us_east_1 } ... }
      #
      # Required even for an environment with no domain — configuration_aliases is a contract of
      # the module, not of the variables — which is why all three roots carry the block.
      configuration_aliases = [aws.us_east_1]
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

# ─────────────────────────────────────────────────────────────────────────────
# BOOTSTRAP — the one config that CREATES the S3 bucket every other config will store
# its state in. It's the chicken that lays the egg: since there is no remote bucket yet
# to hold ITS OWN state, this config uses LOCAL state. You apply it ONCE and then rarely
# touch it again.
#
# Why a separate bucket for "state"? See the explanation in chat — short version: a shared,
# versioned, encrypted, LOCKED home for state so your laptop isn't the only copy and two
# applies can't corrupt it.
# ─────────────────────────────────────────────────────────────────────────────

terraform {
  required_version = ">= 1.9"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  # NOTE: no `backend` block here on purpose — this config keeps LOCAL state.
}

provider "aws" {
  region = "ap-northeast-1"
}

data "aws_caller_identity" "me" {}

# The bucket that will hold dev/staging/prod state files (each under its own key).
# S3 bucket names are GLOBALLY unique across all AWS customers, so we suffix it with
# your account id to guarantee no clash.
resource "aws_s3_bucket" "tfstate" {
  bucket = "agentd-tfstate-${data.aws_caller_identity.me.account_id}"

  tags = {
    Project   = "agentd"
    ManagedBy = "terraform"
    Purpose   = "terraform-remote-state"
  }
}

# Versioning: every state write keeps the previous version → you can roll back a bad apply.
resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  versioning_configuration {
    status = "Enabled"
  }
}

# Encrypt state at rest — it can contain secrets (DB passwords, etc.).
resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Belt-and-braces: this bucket must NEVER be public.
resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket                  = aws_s3_bucket.tfstate.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

output "state_bucket" {
  description = "Name of the S3 bucket that will hold all environment state. Note it for the backend blocks."
  value       = aws_s3_bucket.tfstate.id
}

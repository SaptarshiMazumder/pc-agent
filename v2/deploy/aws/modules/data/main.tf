# modules/data — the durable stores the containers depend on.
#
# NOTE: RDS is intentionally NOT here yet. The accounts app still uses SQLite, so its DB file
# lives on EFS for now. When we teach accounts to speak Postgres, RDS comes back together with
# that app change (they're one unit of work).
#   • Secrets Manager — app secrets (model gateway master key + provider API keys)
#   • EFS             — shared file system (accounts SQLite DB + daemon per-user files)

terraform {
  required_providers {
    aws    = { source = "hashicorp/aws" }
    random = { source = "hashicorp/random" }
  }
}

locals {
  name_prefix = "${var.project}-${var.environment}"
  common_tags = {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

# ─────────────────────────── Secrets ───────────────────────────

# A generated master key the daemon presents to the model gateway (an arbitrary shared secret).
resource "random_password" "master_key" {
  length  = 32
  special = false
}

# App secrets: the generated master key + provider API keys. The keys start as placeholders;
# you set the REAL values later via the AWS CLI, so they never touch git or Terraform state.
resource "aws_secretsmanager_secret" "app" {
  name                    = "${var.project}/${var.environment}/app"
  description             = "App secrets (model gateway master key + provider API keys)"
  recovery_window_in_days = 0 # dev: delete immediately on destroy (no 30-day recycle-bin hold)
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id = aws_secretsmanager_secret.app.id
  secret_string = jsonencode({
    LITELLM_MASTER_KEY = "sk-${random_password.master_key.result}"
    GEMINI_API_KEY     = "REPLACE_ME"
    DEEPSEEK_API_KEY   = "REPLACE_ME"
  })

  # After first creation you edit the real values via the CLI; this stops Terraform from
  # reverting them on the next apply.
  lifecycle {
    ignore_changes = [secret_string]
  }
}

# ─────────────────────────── EFS (shared files) ───────────────────────────

resource "aws_efs_file_system" "main" {
  creation_token = local.name_prefix
  encrypted      = true
  tags           = merge(local.common_tags, { Name = local.name_prefix })
}

# Mount targets: one per subnet, so a task in either AZ can reach the file system.
resource "aws_efs_mount_target" "main" {
  count           = length(var.subnet_ids)
  file_system_id  = aws_efs_file_system.main.id
  subnet_id       = var.subnet_ids[count.index]
  security_groups = [var.efs_sg_id]
}

# Access point: presents the /data subdirectory as the mount root with a fixed user id.
resource "aws_efs_access_point" "data" {
  file_system_id = aws_efs_file_system.main.id

  posix_user {
    uid = 1000
    gid = 1000
  }

  root_directory {
    path = "/data"
    creation_info {
      owner_uid   = 1000
      owner_gid   = 1000
      permissions = "0755"
    }
  }

  tags = merge(local.common_tags, { Name = "${local.name_prefix}-data" })
}

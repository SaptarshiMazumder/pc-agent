# modules/ecr — the container registries for an environment. ONE repository per service
# image, created in a single loop (for_each) instead of four copy-pasted blocks.

locals {
  name_prefix = "${var.project}-${var.environment}"
  common_tags = {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

resource "aws_ecr_repository" "this" {
  for_each = toset(var.services) # one repo per name: gateway, accounts, daemon, web

  name                 = "${local.name_prefix}/${each.key}" # e.g. agentd-dev/gateway
  image_tag_mutability = var.image_tag_mutability
  force_delete         = var.force_delete

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = merge(local.common_tags, {
    Name      = "${local.name_prefix}/${each.key}"
    Component = each.key
  })
}

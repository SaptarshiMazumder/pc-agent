# The container registries: ONE repository per service image, in a single loop over
# the services map.

resource "aws_ecr_repository" "this" {
  for_each = local.services # one repo per name: model-proxy, accounts, daemon, web

  name                 = "${local.name_prefix}/${each.key}" # e.g. agentd-dev/model-proxy
  image_tag_mutability = var.image_tag_mutability
  force_delete         = var.ecr_force_delete

  image_scanning_configuration {
    scan_on_push = true
  }

  # Service renames change the repository name. Create the new repository before
  # removing the old one so the migration can push the image between targeted and
  # full applies; see deploy/LAUNCH.md.
  lifecycle {
    create_before_destroy = true
  }

  tags = merge(local.common_tags, {
    Name      = "${local.name_prefix}/${each.key}"
    Component = each.key
  })
}

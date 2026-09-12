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

# ── expiry ───────────────────────────────────────────────────────────────────
#
# WITHOUT THIS NOTHING IS EVER DELETED, and the deploy pipeline is built to add. redeploy.sh
# pushes `:latest` AND a permanent `:<git-sha>` on every run (that immutable reference is the
# point — a rollback should name a build, not a moving pointer), so every CI deploy leaves a tag
# behind that no process will ever remove. Across eight repositories at roughly a gigabyte an
# image, storage grows with the commit count and the only signal is the bill.
#
# ONE POLICY FOR EVERY REPOSITORY, service and Lambda alike — they all receive the same kind of
# push from the same two scripts, so a repository that opted out would just be the one that
# grows. The map is built here rather than repeated in three files.
#
# RULE ORDER IS EVALUATION ORDER, lowest priority first, and untagged must come first: once the
# count rule has expired an image its layers are untagged, and the 7-day grace is what stops a
# `docker pull` that is mid-flight from losing them underneath it.
locals {
  ecr_repositories = merge(
    { for name, repo in aws_ecr_repository.this : name => repo.name },
    {
      builder  = aws_ecr_repository.builder.name
      publish  = aws_ecr_repository.publish.name
      executor = aws_ecr_repository.executor.name
    }
  )
}

resource "aws_ecr_lifecycle_policy" "expire_old" {
  for_each   = local.ecr_repositories
  repository = each.value

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Untagged layers are rubbish from a replaced tag; keep them a week in case a pull is in flight."
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 7
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Keep the newest ${var.ecr_keep_images} images. :latest is always among them, being the most recent push."
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = var.ecr_keep_images
        }
        action = { type = "expire" }
      },
    ]
  })
}

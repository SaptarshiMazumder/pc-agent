# ─────────────────────────────────────────────────────────────────────────────
# GITHUB OIDC — lets the Deploy workflow authenticate to AWS with NO long-lived keys.
# GitHub hands each workflow run a short-lived OIDC token; AWS trusts it (scoped to THIS
# repo) and issues temporary credentials. Apply ONCE (own LOCAL state, like bootstrap/),
# then paste the output role ARN into the repo secret AWS_DEPLOY_ROLE_ARN.
#
#   terraform -chdir=github-oidc init
#   terraform -chdir=github-oidc apply                      # github_repo now has a default
#   terraform -chdir=github-oidc apply -var github_repo=OWNER/REPO   # a fork
#
# GOTCHA THAT COST A DEPLOY: `github_repo` used to have no default, so a bare `apply` PROMPTED
# for it — and that prompt is easy to mistake for the apply confirmation. Answering "yes" set the
# trust policy to `repo:yes:*` and locked GitHub Actions out of AWS entirely. It now has a default
# and a validation that rejects anything that is not OWNER/REPO.
# ─────────────────────────────────────────────────────────────────────────────
terraform {
  required_version = ">= 1.9"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
    tls = { source = "hashicorp/tls", version = "~> 4.0" }
  }
  # LOCAL state on purpose — account-global, rarely changes.
}

provider "aws" {
  region = "ap-northeast-1"
}

variable "github_repo" {
  description = "The GitHub repo allowed to assume the role, as OWNER/REPO."
  type        = string

  # DEFAULTED, because this variable had no default and that broke deploys. Terraform prompts for
  # a missing variable BEFORE the apply confirmation, and the prompt looks exactly like the
  # confirmation — so `terraform apply` answered with "yes" silently rewrote the trust policy to
  # `repo:yes:*`, and every subsequent workflow run failed with "Not authorized to perform
  # sts:AssumeRoleWithWebIdentity". The role and the OIDC provider were fine; the value was junk.
  #
  # A default is safe here: this is a ROOT module for one repository, not a shared one, and a
  # wrong default cannot widen access — it can only fail to grant it (a fork inheriting this value
  # gets denied, which is the correct outcome). Override with -var for a fork.
  default = "SaptarshiMazumder/pc-agent"

  # The second half of the fix, and the more important one. This is the value that decides WHO may
  # assume a role with deploy permissions, so it deserves to fail loudly rather than accept a word.
  validation {
    condition     = can(regex("^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$", var.github_repo))
    error_message = "github_repo must be OWNER/REPO (e.g. SaptarshiMazumder/pc-agent) — a bare word here silently locks the deploy workflow out of AWS."
  }
}

data "aws_caller_identity" "me" {}

# GitHub's OIDC signing cert — fetched live so we never hardcode a thumbprint that can rotate.
data "tls_certificate" "github" {
  url = "https://token.actions.githubusercontent.com/.well-known/openid-configuration"
}

resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.github.certificates[0].sha1_fingerprint]
}

# Trust: only tokens from THIS repo, audience sts.amazonaws.com, may assume the role.
data "aws_iam_policy_document" "trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repo}:*"] # any branch/tag/PR in this repo
    }
  }
}

# Permissions the Deploy workflow needs: push to ECR, roll ECS, read the ALB dns.
data "aws_iam_policy_document" "deploy" {
  statement {
    sid       = "EcrAuth"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"] # this action does not support resource scoping
  }
  statement {
    sid = "EcrPush"
    actions = [
      "ecr:BatchGetImage", "ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage",
    ]
    resources = ["arn:aws:ecr:*:${data.aws_caller_identity.me.account_id}:repository/agentd-*"]
  }
  statement {
    sid       = "EcsRoll"
    actions   = ["ecs:UpdateService", "ecs:DescribeServices"]
    resources = ["arn:aws:ecs:*:${data.aws_caller_identity.me.account_id}:service/agentd-*/*"]
  }
  # READ-ONLY, and only so a FAILED deploy can explain itself. When a rollout does not complete,
  # the workflow lists the service's stopped tasks and reads their stoppedReason — which is
  # almost always the actual answer ("CannotPullContainerError", "OutOfMemoryError", the
  # container's own exit code). Without these two the diagnostic step dies with an
  # AccessDeniedException instead, taking the verdict with it: the run fails with exit 254 and
  # says nothing about why the service did not deploy.
  #
  # ListTasks is scoped to container-instance rather than task because that is the resource ARN
  # IAM builds for this call (see the denial message); DescribeTasks is scoped to the tasks.
  # Neither can change anything.
  statement {
    sid       = "EcsDiagnoseList"
    actions   = ["ecs:ListTasks"]
    resources = ["arn:aws:ecs:*:${data.aws_caller_identity.me.account_id}:container-instance/agentd-*/*"]
  }
  statement {
    sid       = "EcsDiagnoseDescribe"
    actions   = ["ecs:DescribeTasks"]
    resources = ["arn:aws:ecs:*:${data.aws_caller_identity.me.account_id}:task/agentd-*/*"]
  }
  statement {
    sid       = "AlbLookup"
    actions   = ["elasticloadbalancing:DescribeLoadBalancers"]
    resources = ["*"] # describe does not support resource scoping
  }
  # PUBLISH THE MARKETPLACE (.github/workflows/publish-registry.yml). Writing the registry is the
  # last thing that still required a laptop: the repo, the private signing key and AWS credentials
  # all had to be on one machine, so nobody else could publish and losing that one key file would
  # have permanently ended publishing to this registry (every installed client pins its public
  # half). Moving it into CI needs exactly these three actions on exactly these buckets.
  #
  # Scoped to agentd-*-registry-* — deliberately narrower than the ECR/ECS grants, which cover
  # agentd-*: a deploy role that could write ANY bucket in the account is a much bigger blast
  # radius than one that can only replace marketplace artifacts.
  #
  # GetObject and ListBucket are here because publishing is a read-modify-write: the workflow must
  # read the CURRENT index.json to carry forward bundles it is not republishing. Without the read,
  # each publish would rebuild the index from just that run's packages and unpublish every other
  # agent in the registry.
  statement {
    sid       = "RegistryPublishObjects"
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["arn:aws:s3:::agentd-*-registry-*/*"]
  }
  statement {
    sid       = "RegistryPublishList"
    actions   = ["s3:ListBucket"]
    resources = ["arn:aws:s3:::agentd-*-registry-*"]
  }
  # The registry bucket's name ends in a random suffix (S3 names are globally unique), so the
  # workflow DISCOVERS it instead of hardcoding it — the same trick Deploy uses for the ALB
  # hostname, and what lets both run without Terraform state.
  #
  # This action cannot be resource-scoped: it is the account-level "list bucket names" call. It
  # returns names only — no contents, no policies, no access — and the alternative was writing the
  # bucket name into SSM at apply time and granting a read on that, which is one more resource and
  # one more thing to keep in step for the same outcome.
  statement {
    sid       = "RegistryDiscoverBucket"
    actions   = ["s3:ListAllMyBuckets"]
    resources = ["*"]
  }

  # DEPLOY THE PUBLIC MARKETPLACE PAGE (the `marketplace` job in deploy.yml). A static bundle in
  # its own bucket, in front of the registry above — so the store is reachable by someone who has
  # never installed anything.
  #
  # A SEPARATE STATEMENT rather than widening the registry grant, because these are different
  # blast radii: the registry holds signed artifacts every installed client verifies, and this
  # holds a page. Deleting is allowed HERE and nowhere else — `s3 sync --delete` is what stops a
  # removed asset lingering after a rebuild, and the worst a bug can do to a bucket of build
  # output is force a re-upload.
  statement {
    sid       = "MarketplaceSiteObjects"
    actions   = ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"]
    resources = ["arn:aws:s3:::agentd-*-marketplace-*/*"]
  }
  statement {
    sid       = "MarketplaceSiteList"
    actions   = ["s3:ListBucket"]
    resources = ["arn:aws:s3:::agentd-*-marketplace-*"]
  }

  # The invalidation that follows every upload. Without it the CDN keeps serving the previous
  # index.html and the deploy looks like it did nothing.
  #
  # ListDistributions cannot be resource-scoped (it is the account-level "what distributions exist"
  # call) and is how the workflow FINDS the distribution id from its comment, the same way it
  # discovers the registry bucket rather than hardcoding a generated name. CreateInvalidation and
  # GetDistribution ARE scoped, so the only thing this role can act on is a distribution in this
  # account: CreateInvalidation busts the CDN cache after each upload, and GetDistribution reads the
  # distribution's DomainName for the deploy summary line (the workflow's Invalidate step calls it
  # right after create-invalidation).
  statement {
    sid       = "MarketplaceDiscoverDistribution"
    actions   = ["cloudfront:ListDistributions"]
    resources = ["*"]
  }
  statement {
    sid       = "MarketplaceInvalidate"
    actions   = ["cloudfront:CreateInvalidation", "cloudfront:GetDistribution"]
    resources = ["arn:aws:cloudfront::${data.aws_caller_identity.me.account_id}:distribution/*"]
  }
}

resource "aws_iam_role" "deploy" {
  name               = "agentd-github-deploy"
  assume_role_policy = data.aws_iam_policy_document.trust.json
  tags               = { Project = "agentd", ManagedBy = "terraform", Purpose = "github-actions-deploy" }
}

resource "aws_iam_role_policy" "deploy" {
  name   = "deploy"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy.json
}

output "deploy_role_arn" {
  description = "Paste this into the GitHub repo secret AWS_DEPLOY_ROLE_ARN."
  value       = aws_iam_role.deploy.arn
}

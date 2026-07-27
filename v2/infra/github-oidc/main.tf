# ─────────────────────────────────────────────────────────────────────────────
# GITHUB OIDC — lets the Deploy workflow authenticate to AWS with NO long-lived keys.
# GitHub hands each workflow run a short-lived OIDC token; AWS trusts it (scoped to THIS
# repo) and issues temporary credentials. Apply ONCE (own LOCAL state, like bootstrap/),
# then paste the output role ARN into the repo secret AWS_DEPLOY_ROLE_ARN.
#
#   terraform -chdir=github-oidc init
#   terraform -chdir=github-oidc apply -var github_repo=OWNER/REPO
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
  description = "The GitHub repo allowed to assume the role, as OWNER/REPO (e.g. SaptarshiMazumder/pc-agent)."
  type        = string
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
  statement {
    sid       = "AlbLookup"
    actions   = ["elasticloadbalancing:DescribeLoadBalancers"]
    resources = ["*"] # describe does not support resource scoping
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

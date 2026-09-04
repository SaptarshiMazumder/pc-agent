# The PUBLIC marketplace registry: a plain S3 bucket serving the static files
# `agentd bundle index` produces (index.json + *.agentpkg). No CloudFront, no server:
# the S3 REST endpoint is already HTTPS, and INTEGRITY is enforced client-side — RegistryClient
# verifies each download's sha256 and (with a pinned publisher_key) its ed25519 signature,
# fail-closed. A CDN/custom domain is a later drop-in: only the registry_url in the desktop
# flavors changes.
#
# Publishing is manual by design (the operator runs every aws command):
#   python v2/deploy/registry/publish.py --key <keypair> --bucket <this bucket>

# S3 bucket names are GLOBALLY unique — a random suffix keeps the name collision-proof
# without encoding an account id.
resource "random_id" "suffix" {
  byte_length = 4
}

locals {
  # The one expression that builds the index url, so the desktop flavors (via the output), the
  # hosted daemon (via task env) and any future client all read the SAME string. It used to live
  # only in outputs.tf, which is why the hosted daemon never got one: an output is for humans, and
  # nothing inside the module could reach it.
  registry_index_url = "https://${aws_s3_bucket.registry.bucket}.s3.${var.region}.amazonaws.com/index.json"
}

resource "aws_s3_bucket" "registry" {
  bucket        = "${local.name_prefix}-registry-${random_id.suffix.hex}"
  force_destroy = true # dev: registry contents are always re-publishable build artifacts
  tags          = local.common_tags
}

# World-READ is the point (anyone's desktop app downloads agents from here); writes stay
# owner-only. Both knobs below are required — AWS blocks public policies by default.
resource "aws_s3_bucket_public_access_block" "registry" {
  bucket = aws_s3_bucket.registry.id

  block_public_acls       = true # ACLs stay blocked; access is via the bucket POLICY only
  ignore_public_acls      = true
  block_public_policy     = false
  restrict_public_buckets = false
}

resource "aws_s3_bucket_policy" "public_read" {
  bucket     = aws_s3_bucket.registry.id
  depends_on = [aws_s3_bucket_public_access_block.registry]

  # NotResource carves TWO prefixes OUT of the public grant. With no Allow matching them they
  # fall back to default-deny for the public, while the publish Lambda's own role grants still
  # apply -- so the service can still write and read both.
  #
  #   pending/*   first-publish uploads from creators nobody has admitted yet: unreviewed,
  #               unsigned content that must not be downloadable from the registry's own domain.
  #   orgs/*      ORGANIZATIONS' PRIVATE REGISTRIES. A company's internal agents, its index and
  #               its bundles. Org ids are unguessable in practice, but an id is not a secret --
  #               it rides in tokens, in the UI, in logs and in support threads -- and "nobody
  #               will find the URL" is not an access control an enterprise can be sold. Members
  #               read these through the publish service, which authenticates the caller and
  #               checks membership before handing back presigned links.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid         = "PublicReadRegistry"
      Effect      = "Allow"
      Principal   = "*"
      Action      = "s3:GetObject"
      NotResource = [
        "${aws_s3_bucket.registry.arn}/pending/*",
        "${aws_s3_bucket.registry.arn}/orgs/*",
      ]
    }]
  })
}

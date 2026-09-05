output "bucket" {
  description = "Site bucket name — scripts/deploy.sh syncs the build into this."
  value       = aws_s3_bucket.site.id
}

output "distribution_id" {
  description = "CloudFront distribution id — scripts/deploy.sh invalidates this after a sync."
  value       = aws_cloudfront_distribution.site.id
}

output "url" {
  description = "Where the site is live."
  value       = var.domain_name != "" ? "https://${var.domain_name}" : "https://${aws_cloudfront_distribution.site.domain_name}"
}

output "cloudfront_domain" {
  description = "The distribution's own hostname. Point a CNAME here when attaching a custom domain."
  value       = aws_cloudfront_distribution.site.domain_name
}

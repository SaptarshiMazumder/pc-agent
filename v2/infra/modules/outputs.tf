output "repository_urls" {
  description = "All image push targets (push-images.ps1 reads this)."
  value       = { for name, repo in aws_ecr_repository.this : name => repo.repository_url }
}

output "gateway_repo_url" {
  description = "Where to push the gateway image."
  value       = aws_ecr_repository.this["gateway"].repository_url
}

output "app_url" {
  description = "The public URL of the app (serves once the containers are healthy)."
  value       = "http://${aws_lb.main.dns_name}"
}

# ── Desktop-flavor wiring: these three values go into the flavors' distribution.toml ──

output "accounts_url" {
  description = "[platform] accounts_url for the desktop flavors (sign-in endpoint)."
  value       = "http://${aws_lb.main.dns_name}:4100"
}

output "model_gateway_url" {
  description = "[platform] model_gateway_url for the desktop flavors (platform keys)."
  value       = "http://${aws_lb.main.dns_name}:4000"
}

output "registry_url" {
  description = "[store] registry_url for the desktop flavors (public marketplace index)."
  value       = "https://${aws_s3_bucket.registry.bucket}.s3.${var.region}.amazonaws.com/index.json"
}

output "registry_bucket" {
  description = "Upload target for deploy/registry/publish.py (aws s3 sync)."
  value       = aws_s3_bucket.registry.bucket
}

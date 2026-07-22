# Because we used for_each, the repositories are a MAP keyed by service name. These outputs
# reshape that into simple name -> value maps the caller can index (e.g. urls["gateway"]).

output "repository_urls" {
  description = "Map of service name -> repository URL (the docker push target)."
  value       = { for name, repo in aws_ecr_repository.this : name => repo.repository_url }
}

output "repository_arns" {
  description = "Map of service name -> repository ARN."
  value       = { for name, repo in aws_ecr_repository.this : name => repo.arn }
}

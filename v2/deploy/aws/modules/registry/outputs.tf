output "bucket_name" {
  description = "The registry bucket — publish.py's upload target."
  value       = aws_s3_bucket.registry.bucket
}

output "registry_url" {
  description = "The public index URL — goes into the desktop flavors' [store] registry_url."
  value       = "https://${aws_s3_bucket.registry.bucket}.s3.${var.region}.amazonaws.com/index.json"
}

variable "project" {
  description = "Product name; prefixes the bucket name."
  type        = string
}

variable "environment" {
  description = "Environment name (dev, staging, prod)."
  type        = string
}

variable "region" {
  description = "AWS region (used to render the public HTTPS URL output)."
  type        = string
}

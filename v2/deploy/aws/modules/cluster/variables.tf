variable "project" {
  description = "Product name; prefixes the cluster/log/namespace names."
  type        = string
}

variable "environment" {
  description = "Environment name (dev, staging, prod)."
  type        = string
}

variable "vpc_id" {
  description = "VPC the private DNS namespace is bound to (from the network module)."
  type        = string
}

variable "project" {
  description = "Product name; prefixes resource names and tags."
  type        = string
}

variable "environment" {
  description = "Environment name (dev, staging, prod)."
  type        = string
}

variable "vpc_id" {
  description = "The VPC to create these security groups in (from the network module)."
  type        = string
}

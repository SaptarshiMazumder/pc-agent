variable "project" {
  description = "Product name; prefixes resource names and secret paths."
  type        = string
}

variable "environment" {
  description = "Environment name (dev, staging, prod)."
  type        = string
}

variable "subnet_ids" {
  description = "Subnets for the EFS mount targets (from the network module)."
  type        = list(string)
}

variable "efs_sg_id" {
  description = "Security group for the EFS mount targets (from the security module)."
  type        = string
}

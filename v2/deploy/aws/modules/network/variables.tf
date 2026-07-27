variable "project" {
  description = "Product name; prefixes resource names and tags (e.g. \"agentd\")."
  type        = string
}

variable "environment" {
  description = "Environment name (dev, staging, prod); keeps each environment's names distinct."
  type        = string
}

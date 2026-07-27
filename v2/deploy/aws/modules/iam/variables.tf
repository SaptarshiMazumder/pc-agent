variable "project" {
  description = "Product name; prefixes role names."
  type        = string
}

variable "environment" {
  description = "Environment name (dev, staging, prod); scopes the secrets the role may read."
  type        = string
}

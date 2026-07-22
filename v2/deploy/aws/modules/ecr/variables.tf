variable "project" {
  description = "Product name; prefixes the repository names."
  type        = string
}

variable "environment" {
  description = "Environment name (dev, staging, prod)."
  type        = string
}

variable "services" {
  description = "One repository is created per service image name in this list."
  type        = list(string)
  default     = ["gateway", "accounts", "daemon", "web"]
}

variable "image_tag_mutability" {
  description = "MUTABLE (dev: overwrite tags) or IMMUTABLE (prod: tags are permanent)."
  type        = string
  default     = "MUTABLE"
}

variable "force_delete" {
  description = "Let `terraform destroy` remove a repo even if it still holds images."
  type        = bool
  default     = false
}

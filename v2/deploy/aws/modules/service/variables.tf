# Inputs for ONE containerized service (a task definition + an ECS service + its service-
# discovery registration, optionally attached to an ALB target group and/or mounting EFS).

variable "project" {
  type = string
}
variable "environment" {
  type = string
}

variable "name" {
  description = "Service/container name (gateway, accounts, daemon, web). Also its DNS name."
  type        = string
}
variable "image" {
  description = "Full image URI including tag (e.g. <ecr-url>:latest)."
  type        = string
}
variable "container_port" {
  description = "Port the container listens on."
  type        = number
}
variable "cpu" {
  description = "Fargate CPU units (256 = 0.25 vCPU)."
  type        = number
  default     = 256
}
variable "memory" {
  description = "Fargate memory (MB)."
  type        = number
  default     = 512
}
variable "desired_count" {
  description = "How many copies of the container to keep running."
  type        = number
  default     = 1
}

variable "environment_vars" {
  description = "Plain environment variables: name -> value."
  type        = map(string)
  default     = {}
}
variable "secrets" {
  description = "Secret env vars: env name -> Secrets Manager valueFrom (arn[:jsonKey::])."
  type        = map(string)
  default     = {}
}

# ── Shared infrastructure references (from the other modules) ──
variable "cluster_arn" { type = string }
variable "subnet_ids" { type = list(string) }
variable "security_group_id" { type = string }
variable "execution_role_arn" { type = string }
variable "task_role_arn" { type = string }
variable "log_group_name" { type = string }
variable "region" { type = string }
variable "namespace_id" { type = string }

# ── Optional: expose via the ALB ──
variable "exposed" {
  description = "If true, register this service's containers into an ALB target group."
  type        = bool
  default     = false
}
variable "target_group_arn" {
  description = "ALB target group to register into (required when exposed = true)."
  type        = string
  default     = ""
}

# ── Optional: mount EFS ──
variable "efs_id" {
  type    = string
  default = ""
}
variable "efs_access_point_id" {
  description = "If set, the EFS access point is mounted at mount_path."
  type        = string
  default     = ""
}
variable "mount_path" {
  type    = string
  default = "/data"
}

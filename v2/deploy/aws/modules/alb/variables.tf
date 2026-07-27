variable "project" {
  description = "Product name; prefixes the ALB and target group names."
  type        = string
}

variable "environment" {
  description = "Environment name (dev, staging, prod)."
  type        = string
}

variable "vpc_id" {
  description = "VPC the target groups live in (from the network module)."
  type        = string
}

variable "subnet_ids" {
  description = "Public subnets the ALB spans (from the network module)."
  type        = list(string)
}

variable "alb_sg_id" {
  description = "The ALB's security group (from the security module)."
  type        = string
}

variable "services" {
  description = "Internet-exposed services: name -> { port, health_path }."
  type = map(object({
    port        = number
    health_path = string
  }))
  default = {
    web      = { port = 80, health_path = "/" }
    accounts = { port = 4100, health_path = "/health" }
    # daemon is a WebSocket server: a plain HTTP GET to the WS root returns 426 Upgrade
    # Required. It exposes /healthz specifically for load-balancer liveness (real 200 OK).
    daemon   = { port = 8787, health_path = "/healthz" }
    # model gateway (LiteLLM): PUBLIC in platform-keys mode — desktop daemons authenticate
    # with per-user session tokens (custom auth), so exposure is safe. The liveness path is
    # unauthenticated on the pinned litellm (1.88.1).
    gateway  = { port = 4000, health_path = "/health/liveliness" }
  }
}

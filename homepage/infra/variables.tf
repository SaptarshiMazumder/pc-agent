variable "region" {
  description = "Region for the site bucket. Matches the platform's default; the distribution is global either way."
  type        = string
  default     = "ap-northeast-1"
}

variable "domain_name" {
  description = "Custom hostname for the site, e.g. agentd.dev. Leave empty to serve on the distribution's own *.cloudfront.net name. Requires certificate_arn to take effect."
  type        = string
  default     = ""
}

variable "certificate_arn" {
  description = "ACM certificate for domain_name. MUST be in us-east-1 whatever region this deployment runs in — CloudFront reads certificates only from there. The platform's own CloudFront certificate already covers *.<root_domain> and can be reused."
  type        = string
  default     = ""
}

variable "hosted_zone_id" {
  description = <<-EOT
    Route 53 zone to publish the alias record into. Leave empty to skip DNS entirely
    (you then point a CNAME at the cloudfront_domain output yourself).

    NOTE: this is normally the PLATFORM's zone, created by v2/infra. Writing one record
    into a zone another state owns is deliberate — a domain has one zone, and duplicating
    it here would be worse — but it does mean `terraform destroy` on the platform takes
    this record with it. The record itself is re-created by an apply here.
  EOT
  type        = string
  default     = ""
}

variable "price_class" {
  description = "CloudFront edge coverage. PriceClass_100 (NA + EU) is the cheapest; PriceClass_All adds Asia-Pacific and South America."
  type        = string
  default     = "PriceClass_200"

  validation {
    condition     = contains(["PriceClass_100", "PriceClass_200", "PriceClass_All"], var.price_class)
    error_message = "price_class must be PriceClass_100, PriceClass_200, or PriceClass_All."
  }
}

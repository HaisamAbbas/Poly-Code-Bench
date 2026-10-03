variable "environment" {
  type        = string
  description = "Deployment environment."
}

variable "domain_names" {
  type        = list(string)
  description = "Public hostnames (owner-supplied publication target)."
}

variable "cdn_certificate_arn" {
  type        = string
  description = "ACM certificate in us-east-1 covering domain_names."
  validation {
    condition     = can(regex("^arn:aws[a-z-]*:acm:us-east-1:[0-9]{12}:certificate/", var.cdn_certificate_arn))
    error_message = "CloudFront certificates must be ACM certificates in us-east-1."
  }
}

variable "web_acl_arn" {
  type        = string
  default     = null
  description = "Optional CLOUDFRONT-scope WAF web ACL."
}

variable "price_class" {
  type        = string
  default     = "PriceClass_100"
  description = "CloudFront price class."
}

variable "public_bucket_id" {
  type        = string
  description = "Public projection bucket."
}

variable "public_bucket_regional_domain_name" {
  type        = string
  description = "Public bucket regional domain."
}

variable "public_base_policy_json" {
  type        = string
  description = "Base public bucket policy from modules/artifacts."
}

variable "alb_dns_name" {
  type        = string
  description = "Control load balancer."
}

variable "logs_bucket_domain_name" {
  type        = string
  description = "Access-log bucket domain."
}

variable "tags" {
  type        = map(string)
  default     = {}
  description = "Ownership tags."
}

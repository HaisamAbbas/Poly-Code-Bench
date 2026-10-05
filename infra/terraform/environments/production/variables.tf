variable "account_id" {
  type        = string
  description = "The only AWS account this root may touch (production)."
  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "account_id must be a 12-digit AWS account ID."
  }
}

variable "region" {
  type        = string
  description = "AWS region."
}

variable "bucket_prefix" {
  type        = string
  description = "Globally unique bucket prefix."
}

variable "vpc_cidr" {
  type        = string
  description = "VPC block; must not overlap any other environment."
}

variable "availability_zones" {
  type        = list(string)
  description = "AZs in preference order."
}

variable "az_count" {
  type        = number
  default     = 2
  description = "AZs spanned."
}

variable "signing_key_ids" {
  type        = list(string)
  description = "Signing key IDs; rotation appends, never removes a published key's public half."
}

variable "operator_principal_arns" {
  type        = list(string)
  description = "SSO roles of named operators."
}

variable "approved_guest_ami_id" {
  type        = string
  description = "Promoted guest AMI."
}

variable "guest_instance_type" {
  type        = string
  description = "Disposable guest instance type."
}

variable "performance_instance_type" {
  type        = string
  description = "Fixed performance instance type."
}

variable "performance_hardware_class" {
  type        = string
  description = "Recorded hardware class identity."
}

variable "performance_reserved_instances" {
  type        = number
  description = "Capacity reservation size."
}

variable "database_instance_class" {
  type        = string
  description = "RDS instance class."
}

variable "database_multi_az" {
  type        = bool
  description = "RDS standby. Required in production."
  validation {
    condition     = var.database_multi_az
    error_message = "production requires a Multi-AZ database."
  }
}

variable "database_backup_retention_days" {
  type        = number
  description = "PITR window."
}

variable "backup_vault_lock" {
  type        = bool
  description = "Compliance lock on the backup vault."
}

variable "alb_certificate_arn" {
  type        = string
  description = "Regional ACM certificate for the origin ALB."
}

variable "cdn_certificate_arn" {
  type        = string
  description = "us-east-1 ACM certificate for CloudFront."
}

variable "web_acl_arn" {
  type        = string
  default     = null
  description = "Optional WAF web ACL."
}

variable "domain_names" {
  type        = list(string)
  description = "Public hostnames."
}

variable "services" {
  description = "Control services."
  type = map(object({
    role          = string
    image         = string
    cpu           = number
    memory        = number
    desired_count = number
    port          = optional(number)
    health_path   = optional(string, "/healthz")
    command       = optional(list(string))
    environment   = optional(map(string), {})
    secrets       = optional(map(string), {})
  }))
}

variable "schedules" {
  description = "Scheduled operations tasks."
  type = map(object({
    service    = string
    expression = string
    command    = list(string)
  }))
}

variable "otel_collector_image" {
  type        = string
  default     = null
  description = "Digest-pinned collector sidecar."
}

variable "alert_email_endpoints" {
  type        = list(string)
  default     = []
  description = "On-call alert subscribers."
}

variable "monthly_budget_usd" {
  type        = number
  description = "Owner-approved monthly infrastructure alert threshold (no default); not a hard spend limit."
}

variable "environment" {
  type        = string
  description = "Deployment environment."
  validation {
    condition     = contains(["integration", "staging", "production"], var.environment)
    error_message = "environment must be integration, staging or production."
  }
}

variable "bucket_prefix" {
  type        = string
  description = "Globally unique bucket name prefix owned by the deployment (e.g. an org slug)."
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{2,30}$", var.bucket_prefix))
    error_message = "bucket_prefix must be a short lowercase DNS label."
  }
}

variable "key_arns" {
  type        = map(string)
  description = "KMS key ARNs from modules/keys."
}

variable "provisional_retention_days" {
  type        = number
  default     = 30
  description = "Unreferenced provisional uploads and debug artifacts (T 22.6 default 30)."
}

variable "cancelled_log_retention_days" {
  type        = number
  default     = 90
  description = "Non-published cancelled-run logs (T 22.6 default 90)."
}

variable "noncurrent_version_days" {
  type        = number
  default     = 35
  description = "Noncurrent object versions kept for point-in-time artifact recovery; must exceed the database backup window."
}

variable "access_log_retention_days" {
  type        = number
  default     = 365
  description = "Access log retention."
}

variable "published_object_lock_days" {
  type        = number
  default     = 3650
  description = "Governance-mode retention for public projection objects; withdrawal writes a notice, never deletes."
}

variable "tags" {
  type        = map(string)
  default     = {}
  description = "Ownership tags."
}

variable "environment" {
  type        = string
  description = "Deployment environment."
  validation {
    condition     = contains(["integration", "staging", "production"], var.environment)
    error_message = "environment must be integration, staging or production."
  }
}

variable "data_key_arn" {
  type        = string
  description = "KMS key encrypting the vault."
}

variable "database_arn" {
  type        = string
  description = "RDS instance to back up."
}

variable "bucket_arns" {
  type        = list(string)
  description = "Evidence buckets to back up (internal and public; never hidden)."
}

variable "retention_days" {
  type        = number
  default     = 365
  description = "Daily recovery point retention."
}

variable "vault_lock" {
  type        = bool
  default     = false
  description = "Compliance vault lock; enable in production after the first verified restore."
}

variable "alert_topic_arn" {
  type        = string
  description = "SNS topic for backup/restore failures."
}

variable "tags" {
  type        = map(string)
  default     = {}
  description = "Ownership tags."
}

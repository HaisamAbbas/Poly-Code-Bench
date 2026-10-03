variable "environment" {
  type        = string
  description = "Deployment environment."
  validation {
    condition     = contains(["integration", "staging", "production"], var.environment)
    error_message = "environment must be integration, staging or production."
  }
}

variable "vpc_id" {
  type        = string
  description = "Environment VPC."
}

variable "data_subnet_ids" {
  type        = list(string)
  description = "Isolated data-tier subnets (two or more AZs)."
}

variable "control_security_group_id" {
  type        = string
  description = "Security group of the control services allowed to connect."
}

variable "engine_version" {
  type        = string
  default     = "17.6"
  description = "PostgreSQL version; matches the pinned local development image."
}

variable "instance_class" {
  type        = string
  description = "RDS instance class; sized per environment."
}

variable "allocated_storage_gb" {
  type        = number
  default     = 100
  description = "Initial storage."
}

variable "max_allocated_storage_gb" {
  type        = number
  default     = 500
  description = "Storage autoscaling ceiling."
}

variable "multi_az" {
  type        = bool
  description = "Standby replica; required for production."
}

variable "backup_retention_days" {
  type        = number
  description = "Automated backup / PITR window."
  validation {
    condition     = var.backup_retention_days >= 7 && var.backup_retention_days <= 35
    error_message = "backup_retention_days must be 7-35."
  }
}

variable "data_key_arn" {
  type        = string
  description = "KMS key for storage and Performance Insights."
}

variable "secrets_key_arn" {
  type        = string
  description = "KMS key for the managed master credential."
}

variable "monitoring_role_arn" {
  type        = string
  description = "Enhanced monitoring role (modules/telemetry)."
}

variable "tags" {
  type        = map(string)
  default     = {}
  description = "Ownership tags."
}

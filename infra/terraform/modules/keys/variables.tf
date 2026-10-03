variable "environment" {
  type        = string
  description = "Deployment environment."
  validation {
    condition     = contains(["integration", "staging", "production"], var.environment)
    error_message = "environment must be integration, staging or production."
  }
}

variable "region" {
  type        = string
  description = "AWS region."
}

variable "database_roles" {
  type        = list(string)
  description = "Application database roles that receive their own credential secret."
  default     = ["api", "scheduler", "solve-supervisor", "eval-supervisor", "scorer", "publisher", "migrator", "restore-operator", "ops-reaper"]
}

variable "signing_key_ids" {
  type        = list(string)
  description = "Publication signing key IDs. Rotation appends a new ID; retired IDs stay listed until their secret is scheduled for deletion, and their PUBLIC keys stay published forever."
  validation {
    condition     = length(var.signing_key_ids) >= 1 && alltrue([for id in var.signing_key_ids : can(regex("^[a-z0-9][a-z0-9-]{2,62}$", id))])
    error_message = "At least one signing key ID is required; IDs are lowercase slugs."
  }
}

variable "tags" {
  type        = map(string)
  default     = {}
  description = "Ownership tags."
}

variable "environment" {
  type        = string
  description = "Deployment environment."
}

variable "region" {
  type        = string
  description = "AWS region."
}

variable "bucket_prefix" {
  type        = string
  description = "Bucket name prefix (shared with modules/artifacts)."
}

variable "services" {
  type        = list(string)
  description = "Log group per process role, plus prometheus for workspace logs."
  default     = ["api", "scheduler", "model-gateway", "judge-gateway", "solve-supervisor", "eval-supervisor", "scorer", "publisher", "web", "migrator", "ops-reaper", "admission-operator", "restore-operator", "otel-collector", "prometheus"]
}

variable "log_retention_days" {
  type        = number
  default     = 90
  description = "Service log retention (T 22.6: verbose debug data has bounded retention)."
}

variable "logs_key_arn" {
  type        = string
  description = "KMS key for log groups."
}

variable "alert_rules_path" {
  type        = string
  description = "Path to infra/observability/prometheus/alerts.yaml."
}

variable "alert_email_endpoints" {
  type        = list(string)
  default     = []
  description = "On-call addresses subscribed to the alert topic."
}

variable "hidden_bucket_arn" {
  type        = string
  description = "Hidden bundle bucket ARN (data events)."
}

variable "hidden_bucket_name" {
  type        = string
  description = "Hidden bundle bucket name (metric filter)."
}

variable "database_free_storage_alarm_bytes" {
  type        = number
  default     = 10737418240
  description = "Free-storage alarm threshold (environment configuration, not a scoring input)."
}

variable "tags" {
  type        = map(string)
  default     = {}
  description = "Ownership tags."
}

variable "monthly_budget_usd" {
  type        = number
  description = "Owner-approved monthly infrastructure cap for this environment (AWS Budgets alerts at 50/80/100% and forecast)."
  validation {
    condition     = var.monthly_budget_usd > 0
    error_message = "An explicit positive budget is required; there is no default."
  }
}

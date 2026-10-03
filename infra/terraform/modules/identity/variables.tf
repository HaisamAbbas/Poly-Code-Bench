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

variable "operator_principal_arns" {
  type        = list(string)
  description = "SSO permission-set roles of named operators allowed to assume operator roles (MFA required)."
  validation {
    condition     = length(var.operator_principal_arns) > 0 && alltrue([for arn in var.operator_principal_arns : can(regex("^arn:aws[a-z-]*:iam::[0-9]{12}:role/", arn))])
    error_message = "operator_principal_arns must name concrete IAM role ARNs; wildcards are not accepted."
  }
}

variable "key_arns" {
  type        = map(string)
  description = "KMS key ARNs from modules/keys (data, hidden, secrets, signing, logs)."
}

variable "bucket_arns" {
  type        = map(string)
  description = "Bucket ARNs from modules/artifacts (hidden, internal, public)."
}

variable "launch_template_arns" {
  type        = map(string)
  description = "Lane launch template ARNs from modules/workers and modules/performance (solve, grading, admission, performance)."
}

variable "ecr_repository_arns" {
  type        = list(string)
  description = "Repositories the task execution role may pull from."
}

variable "prometheus_workspace_arn" {
  type        = string
  description = "Managed Prometheus workspace receiving service metrics."
}

variable "tags" {
  type        = map(string)
  default     = {}
  description = "Ownership tags."
}

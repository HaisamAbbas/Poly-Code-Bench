variable "environment" {
  type        = string
  description = "Deployment environment."
}

variable "repositories" {
  type        = list(string)
  description = "Repository names: control services and approved guest images."
  default = [
    "api", "scheduler", "model-gateway", "judge-gateway", "solve-supervisor", "eval-supervisor",
    "scorer", "publisher", "web", "ops",
    "guest-python", "guest-rust", "guest-c", "guest-cpp", "guest-go", "guest-java", "guest-javascript",
  ]
}

variable "data_key_arn" {
  type        = string
  description = "KMS key for image encryption."
}

variable "tags" {
  type        = map(string)
  default     = {}
  description = "Ownership tags."
}

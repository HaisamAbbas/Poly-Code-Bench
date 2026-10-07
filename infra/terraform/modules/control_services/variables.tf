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

variable "vpc_id" {
  type        = string
  description = "Environment VPC."
}

variable "control_security_group_id" {
  type        = string
  description = "Control-service security group from modules/network."
}

variable "public_subnet_ids" {
  type        = list(string)
  description = "Public subnets for the load balancer."
}

variable "control_subnet_ids" {
  type        = list(string)
  description = "Control-tier subnets for services and scheduled tasks."
}

variable "alb_certificate_arn" {
  type        = string
  description = "ACM certificate for the origin load balancer."
}

variable "access_log_bucket" {
  type        = string
  description = "Bucket receiving ALB access logs."
}

variable "task_execution_role_arn" {
  type        = string
  description = "ECS task execution role."
}

variable "task_role_arns" {
  type        = map(string)
  description = "Task role ARN per process role (modules/identity)."
}

variable "services" {
  description = "Control services keyed by service name. `desired_count = 0` defines a run-on-demand task (migrator, restore rehearsal)."
  type = map(object({
    role          = string
    image         = string
    cpu           = number
    memory        = number
    desired_count = number
    port          = optional(number)
    health_path   = optional(string, "/readyz")
    command       = optional(list(string))
    environment   = optional(map(string), {})
    secrets       = optional(map(string), {})
  }))
  validation {
    condition     = alltrue([for svc in values(var.services) : can(regex("@sha256:[0-9a-f]{64}$", svc.image))])
    error_message = "Every service image must be pinned by @sha256 digest."
  }
  validation {
    condition = alltrue([
      for svc in values(var.services) : svc.desired_count >= 0 &&
      svc.desired_count == floor(svc.desired_count) && (
        svc.desired_count == 0 || (
          !strcontains(svc.image, "REQUIRED") &&
          !endswith(svc.image, "@sha256:0000000000000000000000000000000000000000000000000000000000000000")
        )
      )
    ])
    error_message = "Service desired_count must be a nonnegative integer, and enabled services require resolved image digests."
  }
  validation {
    condition     = contains(keys(var.services), "api") && contains(keys(var.services), "web") && var.services["api"].port != null && var.services["web"].port != null
    error_message = "services must include api and web with ports (the ALB and Service Connect route to both)."
  }
}

variable "schedules" {
  description = "Scheduled operations tasks: service to run, schedule expression, command override."
  type = map(object({
    service    = string
    expression = string
    command    = list(string)
  }))
  default = {}
}

variable "otel_endpoint" {
  type        = string
  description = "Collector endpoint advertised to processes (PCB_OTEL_ENDPOINT)."
}

variable "metrics_port" {
  type        = number
  default     = 9464
  description = "Port where processes expose Prometheus metrics (PCB_METRICS_PORT)."
}

variable "otel_collector_image" {
  type        = string
  default     = null
  description = "Digest-pinned OpenTelemetry collector sidecar image; null disables the sidecar."
  validation {
    condition     = var.otel_collector_image == null || can(regex("@sha256:[0-9a-f]{64}$", var.otel_collector_image))
    error_message = "otel_collector_image must be pinned by digest."
  }
}

variable "prometheus_remote_write_url" {
  type        = string
  description = "Managed Prometheus remote-write endpoint."
}

variable "tags" {
  type        = map(string)
  default     = {}
  description = "Ownership tags."
}

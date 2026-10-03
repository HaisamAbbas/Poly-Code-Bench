variable "environment" {
  type        = string
  description = "Deployment environment; staging and production never share a network."
  validation {
    condition     = contains(["integration", "staging", "production"], var.environment)
    error_message = "environment must be integration, staging or production."
  }
}

variable "region" {
  type        = string
  description = "AWS region the network is created in."
}

variable "vpc_cidr" {
  type        = string
  description = "Dedicated VPC block for this environment."
  validation {
    condition     = can(cidrhost(var.vpc_cidr, 0))
    error_message = "vpc_cidr must be a valid IPv4 CIDR block."
  }
}

variable "subnet_newbits" {
  type        = number
  default     = 5
  description = "Additional prefix bits per tier/AZ subnet (5 on a /16 gives /21 subnets)."
}

variable "availability_zones" {
  type        = list(string)
  description = "Candidate availability zones, in preference order."
}

variable "az_count" {
  type        = number
  default     = 2
  description = "Number of AZs to span; RDS subnet groups require at least two."
  validation {
    condition     = var.az_count >= 2 && var.az_count <= 3
    error_message = "az_count must be 2 or 3."
  }
}

variable "flow_log_group_arn" {
  type        = string
  description = "CloudWatch log group receiving VPC flow logs (from modules/telemetry)."
}

variable "flow_log_role_arn" {
  type        = string
  description = "Role allowed to deliver VPC flow logs (from modules/identity)."
}

variable "tags" {
  type        = map(string)
  default     = {}
  description = "Ownership tags applied to every resource."
}

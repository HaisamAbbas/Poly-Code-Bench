variable "region" {
  type        = string
  description = "Approved AWS region for the isolated worker fleet."
  validation {
    condition     = can(regex("^[a-z]{2}(-gov)?-[a-z]+-[0-9]$", var.region))
    error_message = "region must be an explicit AWS region identifier."
  }
}

variable "vpc_id" {
  type        = string
  description = "Dedicated worker VPC owned by the authorized deployment."
}

variable "control_security_group_id" {
  type        = string
  description = "Supervisor-only security group; never a public or user-controlled CIDR."
}

variable "private_subnet_ids" {
  type        = map(string)
  description = "Three distinct private subnets keyed by solve, grading, admission."
  validation {
    condition     = alltrue([for lane in ["solve", "grading", "admission"] : contains(keys(var.private_subnet_ids), lane)]) && length(distinct(values(var.private_subnet_ids))) == 3
    error_message = "All three execution lanes require separate private subnets."
  }
}

variable "approved_ami_id" {
  type        = string
  description = "Reviewed immutable AMI with pinned Docker, guest agent and SSH host key."
  validation {
    condition     = can(regex("^ami-[0-9a-f]+$", var.approved_ami_id))
    error_message = "approved_ami_id must be a concrete AMI identity."
  }
}

variable "instance_type" {
  type        = string
  description = "Approved disposable guest class."
}

variable "common_tags" {
  type        = map(string)
  default     = {}
  description = "Deployment ownership and environment tags."
}

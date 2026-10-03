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

variable "supervisor_security_group_id" {
  type        = string
  description = "Control-service security group from which supervisors open the SSH control channel."
}

variable "lane_subnet_ids" {
  type        = map(string)
  description = "One isolated subnet per lane: solve, grading, admission."
}

variable "approved_ami_id" {
  type        = string
  description = "Promoted guest AMI from the reviewed image manifest."
}

variable "instance_type" {
  type        = string
  description = "Disposable guest instance class."
}

variable "tags" {
  type        = map(string)
  default     = {}
  description = "Ownership tags."
}

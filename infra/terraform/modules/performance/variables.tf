variable "environment" {
  type        = string
  description = "Deployment environment."
}

variable "vpc_id" {
  type        = string
  description = "Environment VPC."
}

variable "subnet_id" {
  type        = string
  description = "Isolated performance subnet in `availability_zone`."
}

variable "availability_zone" {
  type        = string
  description = "AZ of the capacity reservation."
}

variable "supervisor_security_group_id" {
  type        = string
  description = "Control-service security group of the eval supervisor."
}

variable "approved_ami_id" {
  type        = string
  description = "Promoted guest AMI."
  validation {
    condition     = can(regex("^ami-[0-9a-f]+$", var.approved_ami_id))
    error_message = "approved_ami_id must be a concrete AMI identity."
  }
}

variable "instance_type" {
  type        = string
  description = "The single fixed performance instance type."
}

variable "hardware_class" {
  type        = string
  description = "Recorded hardware class identity, e.g. aws-c7i-2xlarge-dedicated-v1."
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]{2,63}$", var.hardware_class))
    error_message = "hardware_class must be a lowercase slug."
  }
}

variable "reserved_instances" {
  type        = number
  default     = 0
  description = "Instances held in the capacity reservation (0 disables the reservation, e.g. staging)."
}

variable "tags" {
  type        = map(string)
  default     = {}
  description = "Ownership tags."
}

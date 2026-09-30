provider "aws" {
  region = var.region
}

locals {
  lanes = toset(["solve", "grading", "admission"])
}

resource "aws_security_group" "guest" {
  for_each    = local.lanes
  name_prefix = "pcb-${each.key}-sandbox-"
  description = "Disposable PolyCodeBench ${each.key} guest control channel only"
  vpc_id      = var.vpc_id

  ingress {
    description     = "SSH forced-command control from the supervisor identity boundary"
    from_port       = 22
    to_port         = 22
    protocol        = "tcp"
    security_groups = [var.control_security_group_id]
  }

  # Guest VM has no internet egress. Candidate Docker containers separately use network=none.
  tags   = merge(var.common_tags, { Name = "pcb-${each.key}-sandbox", "pcb:lane" = each.key })
}

resource "aws_launch_template" "guest" {
  for_each               = local.lanes
  name_prefix             = "pcb-${each.key}-sandbox-"
  image_id                = var.approved_ami_id
  instance_type           = var.instance_type
  update_default_version  = true
  disable_api_termination = false

  metadata_options {
    http_endpoint               = "disabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
    instance_metadata_tags      = "disabled"
  }

  network_interfaces {
    associate_public_ip_address = false
    delete_on_termination       = true
    subnet_id                   = var.private_subnet_ids[each.key]
    security_groups             = [aws_security_group.guest[each.key].id]
  }

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      encrypted             = true
      delete_on_termination = true
      volume_type           = "gp3"
      volume_size           = 16
    }
  }

  # Deliberately no iam_instance_profile: candidates and the guest supervisor have no AWS role.
  tag_specifications {
    resource_type = "instance"
    tags          = merge(var.common_tags, { "pcb:owner" = "polycodebench", "pcb:lane" = each.key })
  }

  tag_specifications {
    resource_type = "volume"
    tags          = merge(var.common_tags, { "pcb:owner" = "polycodebench", "pcb:lane" = each.key })
  }
}

output "sandbox_security_group_ids" {
  value       = { for lane, group in aws_security_group.guest : lane => group.id }
  description = "Lane-scoped guest ingress groups for the supervisor."
}

output "sandbox_launch_template_ids" {
  value       = { for lane, template in aws_launch_template.guest : lane => template.id }
  description = "Lane-scoped disposable guest launch templates."
}

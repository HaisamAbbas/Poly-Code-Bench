# Dedicated performance capacity with a fixed hardware class (A 15.1, T 13).
#
# Performance guests use a single pinned instance type on dedicated tenancy inside an
# on-demand capacity reservation, so a release's efficiency measurements come from one
# hardware class. Changing `hardware_class` or `instance_type` creates a new evaluation
# identity (T 22.3); the tag below is what the eval supervisor records into evidence.
# No GPU inference pool is colocated here.

locals {
  name = "pcb-${var.environment}"
  tags = merge(var.tags, {
    "pcb:environment"    = var.environment
    "pcb:hardware-class" = var.hardware_class
  })
}

resource "aws_ec2_capacity_reservation" "this" {
  count                   = var.reserved_instances > 0 ? 1 : 0
  instance_type           = var.instance_type
  instance_platform       = "Linux/UNIX"
  availability_zone       = var.availability_zone
  instance_count          = var.reserved_instances
  tenancy                 = "dedicated"
  instance_match_criteria = "targeted"
  end_date_type           = "unlimited"
  tags                    = merge(local.tags, { Name = "${local.name}-performance" })
}

resource "aws_security_group" "guest" {
  name_prefix = "${local.name}-performance-"
  description = "Performance guest control channel only; no egress"
  vpc_id      = var.vpc_id

  ingress {
    description     = "SSH forced-command control from the eval supervisor"
    from_port       = 22
    to_port         = 22
    protocol        = "tcp"
    security_groups = [var.supervisor_security_group_id]
  }

  tags = merge(local.tags, { Name = "${local.name}-performance", "pcb:lane" = "performance" })
}

resource "aws_launch_template" "guest" {
  name_prefix             = "${local.name}-performance-"
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

  placement {
    tenancy = "dedicated"
  }

  dynamic "capacity_reservation_specification" {
    for_each = var.reserved_instances > 0 ? [1] : []
    content {
      capacity_reservation_target {
        capacity_reservation_id = aws_ec2_capacity_reservation.this[0].id
      }
    }
  }

  network_interfaces {
    associate_public_ip_address = false
    delete_on_termination       = true
    subnet_id                   = var.subnet_id
    security_groups             = [aws_security_group.guest.id]
  }

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      encrypted             = true
      delete_on_termination = true
      volume_type           = "gp3"
      volume_size           = 16
      iops                  = 3000
      throughput            = 125
    }
  }

  # No iam_instance_profile, as for every execution guest.
  tag_specifications {
    resource_type = "instance"
    tags          = merge(local.tags, { "pcb:owner" = "polycodebench", "pcb:lane" = "performance" })
  }

  tag_specifications {
    resource_type = "volume"
    tags          = merge(local.tags, { "pcb:owner" = "polycodebench", "pcb:lane" = "performance" })
  }
}

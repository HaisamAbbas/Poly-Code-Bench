# Disposable execution guests for solve, grading and admission (T 10.2).
#
# Guests are launched one per stage by the lane supervisor (Ec2VmSandboxProvider) from these
# launch templates; there is no Auto Scaling group, so nothing outlives its stage except
# by failure. Orphans are reclaimed by the scheduled `pcb-ops orphans sweep` task
# (modules/control_services) and alerted on at TTL + 10 minutes (modules/telemetry).

locals {
  tags = merge(var.tags, { "pcb:environment" = var.environment })
}

module "lanes" {
  source = "../../../sandbox/aws"

  vpc_id                    = var.vpc_id
  control_security_group_id = var.supervisor_security_group_id
  private_subnet_ids        = var.lane_subnet_ids
  approved_ami_id           = var.approved_ami_id
  instance_type             = var.instance_type
  common_tags               = local.tags
}

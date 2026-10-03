# Network boundaries for one PolyCodeBench environment (T 22.3, A 15.1).
#
# Tiers:
#   public      - load balancer only; the only subnets with an internet gateway route.
#   control     - trusted control services; egress through NAT for provider/judge APIs.
#   data        - managed PostgreSQL; no default route at all.
#   solve/grading/admission/performance - disposable execution guests; no default route,
#                 reachable only from the supervisor security group (see modules/workers).
# Execution subnets reach AWS APIs through nothing: guests have no role, no IMDS and no egress.

locals {
  name = "pcb-${var.environment}"
  tiers = {
    public      = 0
    control     = 1
    data        = 2
    solve       = 3
    grading     = 4
    admission   = 5
    performance = 6
  }
  execution_tiers = toset(["solve", "grading", "admission", "performance"])
  azs             = slice(var.availability_zones, 0, var.az_count)
  # One subnet per tier per AZ, carved as /20s (or smaller, per vpc size) from the VPC block.
  subnets = merge([
    for tier, tier_index in local.tiers : {
      for az_index, az in local.azs :
      "${tier}-${az}" => {
        tier = tier
        az   = az
        cidr = cidrsubnet(var.vpc_cidr, var.subnet_newbits, tier_index * var.az_count + az_index)
      }
    }
  ]...)
  tags = merge(var.tags, { "pcb:environment" = var.environment })
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = merge(local.tags, { Name = local.name })
}

# The default security group of a new VPC allows all intra-group traffic; strip it.
resource "aws_default_security_group" "this" {
  vpc_id = aws_vpc.this.id
  tags   = merge(local.tags, { Name = "${local.name}-default-deny" })
}

resource "aws_subnet" "this" {
  for_each                = local.subnets
  vpc_id                  = aws_vpc.this.id
  cidr_block              = each.value.cidr
  availability_zone       = each.value.az
  map_public_ip_on_launch = false
  tags = merge(local.tags, {
    Name       = "${local.name}-${each.key}"
    "pcb:tier" = each.value.tier
  })
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = merge(local.tags, { Name = local.name })
}

resource "aws_eip" "nat" {
  domain = "vpc"
  tags   = merge(local.tags, { Name = "${local.name}-nat" })
}

resource "aws_nat_gateway" "control" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.this["public-${local.azs[0]}"].id
  tags          = merge(local.tags, { Name = "${local.name}-control-egress" })
  depends_on    = [aws_internet_gateway.this]
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }
  tags = merge(local.tags, { Name = "${local.name}-public" })
}

resource "aws_route_table" "control" {
  vpc_id = aws_vpc.this.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.control.id
  }
  tags = merge(local.tags, { Name = "${local.name}-control" })
}

# Data and execution tiers: local VPC route only. No internet, no NAT.
resource "aws_route_table" "isolated" {
  for_each = toset(["data", "solve", "grading", "admission", "performance"])
  vpc_id   = aws_vpc.this.id
  tags     = merge(local.tags, { Name = "${local.name}-${each.key}-isolated", "pcb:tier" = each.key })
}

resource "aws_route_table_association" "this" {
  for_each  = local.subnets
  subnet_id = aws_subnet.this[each.key].id
  route_table_id = (
    each.value.tier == "public" ? aws_route_table.public.id :
    each.value.tier == "control" ? aws_route_table.control.id :
    aws_route_table.isolated[each.value.tier].id
  )
}

# Execution-tier NACL: guests may only answer the supervisor's SSH control channel.
resource "aws_network_acl" "execution" {
  for_each   = local.execution_tiers
  vpc_id     = aws_vpc.this.id
  subnet_ids = [for key, subnet in local.subnets : aws_subnet.this[key].id if subnet.tier == each.key]

  dynamic "ingress" {
    for_each = { for index, az in local.azs : index => az }
    content {
      rule_no    = 100 + ingress.key
      protocol   = "tcp"
      action     = "allow"
      cidr_block = local.subnets["control-${ingress.value}"].cidr
      from_port  = 22
      to_port    = 22
    }
  }

  dynamic "egress" {
    for_each = { for index, az in local.azs : index => az }
    content {
      rule_no    = 100 + egress.key
      protocol   = "tcp"
      action     = "allow"
      cidr_block = local.subnets["control-${egress.value}"].cidr
      from_port  = 1024
      to_port    = 65535
    }
  }

  tags = merge(local.tags, { Name = "${local.name}-${each.key}-execution", "pcb:tier" = each.key })
}

# Control-tier access to AWS APIs without traversing the NAT.
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.control.id]
  tags              = merge(local.tags, { Name = "${local.name}-s3" })
}

resource "aws_security_group" "endpoints" {
  name_prefix = "${local.name}-endpoints-"
  description = "Interface endpoints reachable from control services only"
  vpc_id      = aws_vpc.this.id
  tags        = merge(local.tags, { Name = "${local.name}-endpoints" })
}

resource "aws_vpc_security_group_ingress_rule" "endpoints_from_control" {
  for_each          = { for index, az in local.azs : index => az }
  security_group_id = aws_security_group.endpoints.id
  description       = "HTTPS from control subnet ${each.value}"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = local.subnets["control-${each.value}"].cidr
}

resource "aws_vpc_endpoint" "interface" {
  for_each            = toset(["secretsmanager", "kms", "sts", "logs", "ecr.api", "ecr.dkr", "ec2", "monitoring", "aps-workspaces"])
  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.${var.region}.${each.key}"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true
  subnet_ids          = [for az in local.azs : aws_subnet.this["control-${az}"].id]
  security_group_ids  = [aws_security_group.endpoints.id]
  tags                = merge(local.tags, { Name = "${local.name}-${replace(each.key, ".", "-")}" })
}

resource "aws_flow_log" "vpc" {
  vpc_id                   = aws_vpc.this.id
  traffic_type             = "ALL"
  log_destination_type     = "cloud-watch-logs"
  log_destination          = var.flow_log_group_arn
  iam_role_arn             = var.flow_log_role_arn
  max_aggregation_interval = 60
  tags                     = merge(local.tags, { Name = "${local.name}-flow" })
}

# Control-service security group. Lives here (not in control_services) because the database,
# worker and performance modules reference it as their only allowed source.
resource "aws_security_group" "control" {
  name_prefix = "${local.name}-control-"
  description = "Control services: egress to AWS APIs, DB, guests and approved provider endpoints"
  vpc_id      = aws_vpc.this.id
  tags        = merge(local.tags, { Name = "${local.name}-control" })
}

resource "aws_vpc_security_group_egress_rule" "control_https" {
  security_group_id = aws_security_group.control.id
  description       = "HTTPS to AWS endpoints and approved model/judge providers (via NAT)"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "control_postgres" {
  security_group_id = aws_security_group.control.id
  description       = "PostgreSQL inside the VPC"
  ip_protocol       = "tcp"
  from_port         = 5432
  to_port           = 5432
  cidr_ipv4         = var.vpc_cidr
}

resource "aws_vpc_security_group_egress_rule" "control_guest_ssh" {
  security_group_id = aws_security_group.control.id
  description       = "Forced-command SSH control channel to disposable guests"
  ip_protocol       = "tcp"
  from_port         = 22
  to_port           = 22
  cidr_ipv4         = var.vpc_cidr
}


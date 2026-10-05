# One complete PolyCodeBench environment. Called by infra/terraform/environments/<env>; the
# environment roots hold only parameters, provider/account guards and state configuration.
#
# Dependency order (no cycles):
#   keys -> artifacts -> telemetry -> network -> workers/performance/registry -> identity
#        -> database/backup -> control_services -> public_delivery

locals {
  tags = merge(var.tags, {
    "pcb:environment" = var.environment
    "pcb:managed-by"  = "terraform"
    "pcb:stack"       = "polycodebench"
  })
  service_images = { for name, svc in var.services : name => svc.image }
  lane_subnets = {
    for lane in ["solve", "grading", "admission"] : lane => module.network.subnet_ids_by_tier[lane][0]
  }
}

module "keys" {
  source          = "../keys"
  environment     = var.environment
  region          = var.region
  signing_key_ids = var.signing_key_ids
  tags            = local.tags
}

module "artifacts" {
  source        = "../artifacts"
  environment   = var.environment
  bucket_prefix = var.bucket_prefix
  key_arns      = module.keys.key_arns
  tags          = local.tags
}

module "telemetry" {
  source                = "../telemetry"
  environment           = var.environment
  region                = var.region
  bucket_prefix         = var.bucket_prefix
  logs_key_arn          = module.keys.key_arns["logs"]
  alert_rules_path      = var.alert_rules_path
  alert_email_endpoints = var.alert_email_endpoints
  monthly_budget_usd    = var.monthly_budget_usd
  hidden_bucket_arn     = module.artifacts.bucket_arns["hidden"]
  hidden_bucket_name    = module.artifacts.bucket_names["hidden"]
  tags                  = local.tags
}

module "network" {
  source             = "../network"
  environment        = var.environment
  region             = var.region
  vpc_cidr           = var.vpc_cidr
  availability_zones = var.availability_zones
  az_count           = var.az_count
  flow_log_group_arn = module.telemetry.flow_log_group_arn
  flow_log_role_arn  = module.telemetry.flow_log_role_arn
  tags               = local.tags
}

module "registry" {
  source       = "../registry"
  environment  = var.environment
  data_key_arn = module.keys.key_arns["data"]
  tags         = local.tags
}

module "workers" {
  source                       = "../workers"
  environment                  = var.environment
  vpc_id                       = module.network.vpc_id
  supervisor_security_group_id = module.network.control_security_group_id
  lane_subnet_ids              = local.lane_subnets
  approved_ami_id              = var.approved_guest_ami_id
  instance_type                = var.guest_instance_type
  tags                         = local.tags
}

module "performance" {
  source                       = "../performance"
  environment                  = var.environment
  vpc_id                       = module.network.vpc_id
  subnet_id                    = module.network.subnet_ids_by_tier["performance"][0]
  availability_zone            = var.availability_zones[0]
  supervisor_security_group_id = module.network.control_security_group_id
  approved_ami_id              = var.approved_guest_ami_id
  instance_type                = var.performance_instance_type
  hardware_class               = var.performance_hardware_class
  reserved_instances           = var.performance_reserved_instances
  tags                         = local.tags
}

module "identity" {
  source                   = "../identity"
  environment              = var.environment
  region                   = var.region
  operator_principal_arns  = var.operator_principal_arns
  key_arns                 = module.keys.key_arns
  bucket_arns              = module.artifacts.bucket_arns
  launch_template_arns     = merge(module.workers.launch_template_arns, { performance = module.performance.launch_template_arn })
  ecr_repository_arns      = module.registry.repository_arns
  prometheus_workspace_arn = module.telemetry.prometheus_workspace_arn
  tags                     = local.tags
}

module "database" {
  source                    = "../database"
  environment               = var.environment
  vpc_id                    = module.network.vpc_id
  data_subnet_ids           = module.network.subnet_ids_by_tier["data"]
  control_security_group_id = module.network.control_security_group_id
  instance_class            = var.database_instance_class
  multi_az                  = var.database_multi_az
  backup_retention_days     = var.database_backup_retention_days
  data_key_arn              = module.keys.key_arns["data"]
  secrets_key_arn           = module.keys.key_arns["secrets"]
  monitoring_role_arn       = module.telemetry.rds_monitoring_role_arn
  tags                      = local.tags
}

module "backup" {
  source          = "../backup"
  environment     = var.environment
  data_key_arn    = module.keys.key_arns["data"]
  database_arn    = module.database.instance_arn
  bucket_arns     = [module.artifacts.bucket_arns["internal"], module.artifacts.bucket_arns["public"]]
  vault_lock      = var.backup_vault_lock
  alert_topic_arn = module.telemetry.alert_topic_arn
  tags            = local.tags
}

module "control_services" {
  source                    = "../control_services"
  environment               = var.environment
  region                    = var.region
  vpc_id                    = module.network.vpc_id
  control_security_group_id = module.network.control_security_group_id
  public_subnet_ids         = module.network.subnet_ids_by_tier["public"]
  control_subnet_ids        = module.network.subnet_ids_by_tier["control"]
  alb_certificate_arn       = var.alb_certificate_arn
  access_log_bucket         = module.artifacts.bucket_names["logs"]
  task_execution_role_arn   = module.identity.task_execution_role_arn
  task_role_arns            = module.identity.service_role_arns
  services = {
    for name, service in var.services : name => merge(service, {
      # Database credentials are passed as secret references owned by this stack, never as
      # plaintext environment variables in a tfvars file or task definition.
      secrets = merge(
        try(service.secrets, {}),
        contains(keys(module.keys.database_secret_arns), service.role) ? {
          PCB_DATABASE_URL = module.keys.database_secret_arns[service.role]
        } : {},
        service.role == "api" ? {
          PCB_CURSOR_SIGNING_KEY = module.keys.cursor_secret_arn
        } : {}
      )
    })
  }
  schedules                   = var.schedules
  otel_endpoint               = "http://127.0.0.1:4318"
  otel_collector_image        = var.otel_collector_image
  prometheus_remote_write_url = module.telemetry.prometheus_remote_write_url
  tags                        = local.tags
}

module "public_delivery" {
  source                             = "../public_delivery"
  environment                        = var.environment
  domain_names                       = var.domain_names
  cdn_certificate_arn                = var.cdn_certificate_arn
  web_acl_arn                        = var.web_acl_arn
  public_bucket_id                   = module.artifacts.public_bucket_id
  public_bucket_regional_domain_name = module.artifacts.public_bucket_regional_domain_name
  public_base_policy_json            = module.artifacts.public_base_policy_json
  alb_dns_name                       = module.control_services.alb_dns_name
  logs_bucket_domain_name            = module.artifacts.logs_bucket_domain_name
  tags                               = local.tags
}

data "aws_caller_identity" "current" {}

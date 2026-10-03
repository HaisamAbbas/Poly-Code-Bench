# production root: provider/account guard, state backend and parameters only.
# Module definitions live in ../../modules; nothing environment-specific is defined there.

terraform {
  required_version = ">= 1.8.5, < 2.0.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "= 6.36.0"
    }
  }
  # Partial configuration: `terraform init -backend-config=backend.hcl` (see backend.hcl.example).
  # State for production lives in its own bucket/key/lock table; it is never shared with another environment.
  backend "s3" {}
}

provider "aws" {
  region = var.region
  # Terraform refuses to plan or apply when the credentials belong to any other account.
  allowed_account_ids = [var.account_id]
  default_tags {
    tags = {
      "pcb:environment" = "production"
      "pcb:managed-by"  = "terraform"
    }
  }
}

module "stack" {
  source = "../../modules/stack"

  environment                    = "production"
  region                         = var.region
  bucket_prefix                  = var.bucket_prefix
  vpc_cidr                       = var.vpc_cidr
  availability_zones             = var.availability_zones
  az_count                       = var.az_count
  signing_key_ids                = var.signing_key_ids
  operator_principal_arns        = var.operator_principal_arns
  approved_guest_ami_id          = var.approved_guest_ami_id
  guest_instance_type            = var.guest_instance_type
  performance_instance_type      = var.performance_instance_type
  performance_hardware_class     = var.performance_hardware_class
  performance_reserved_instances = var.performance_reserved_instances
  database_instance_class        = var.database_instance_class
  database_multi_az              = var.database_multi_az
  database_backup_retention_days = var.database_backup_retention_days
  backup_vault_lock              = var.backup_vault_lock
  alb_certificate_arn            = var.alb_certificate_arn
  cdn_certificate_arn            = var.cdn_certificate_arn
  web_acl_arn                    = var.web_acl_arn
  domain_names                   = var.domain_names
  services                       = var.services
  schedules                      = var.schedules
  otel_collector_image           = var.otel_collector_image
  alert_rules_path               = "${path.root}/../../../observability/prometheus/alerts.yaml"
  alert_email_endpoints          = var.alert_email_endpoints
  monthly_budget_usd             = var.monthly_budget_usd
}

output "deployment" {
  value       = module.stack
  description = "Inputs for config/environments/production.yaml (pcb-ops env reconcile)."
}

# These outputs are the inputs to config/environments/<env>.yaml. After an apply, run
#   terraform output -json > ../../../../config/environments/<env>.terraform-output.json
#   uv run --offline --locked --all-packages pcb-ops env reconcile --env <env>
# which fails if the application manifest disagrees with what was deployed.

output "environment" {
  value       = var.environment
  description = "Environment name."
}

output "region" {
  value       = var.region
  description = "Region that owns the environment resources."
}

output "account_id" {
  value       = data.aws_caller_identity.current.account_id
  description = "Account the stack was applied to."
}

output "service_role_arns" {
  value       = module.identity.service_role_arns
  description = "Task role per process role."
}

output "operator_role_arns" {
  value       = module.identity.operator_role_arns
  description = "Operator roles."
}

output "bucket_names" {
  value       = module.artifacts.bucket_names
  description = "Artifact buckets."
}

output "database_endpoint" {
  value       = module.database.endpoint
  description = "Database host."
}

output "database_instance_identifier" {
  value       = module.database.instance_identifier
  description = "PITR source identifier."
}

output "database_secret_arns" {
  value       = module.keys.database_secret_arns
  description = "Per-role DSN secret references."
}

output "signing_secret_arns" {
  value       = module.keys.signing_secret_arns
  description = "Signing key references."
}

output "secret_namespaces" {
  value = {
    model = module.keys.model_secret_namespace
    judge = module.keys.judge_secret_namespace
  }
  description = "Gateway secret namespaces."
}

output "launch_template_ids" {
  value       = merge(module.workers.launch_template_ids, { performance = module.performance.launch_template_id })
  description = "Lane launch templates."
}

output "launch_template_versions" {
  value       = merge(module.workers.launch_template_versions, { performance = module.performance.launch_template_version })
  description = "Pinned launch-template versions for the worker environment manifest."
}

output "guest_security_group_ids" {
  value       = module.workers.guest_security_group_ids
  description = "Lane guest security groups."
}

output "lane_subnet_ids" {
  value       = local.lane_subnets
  description = "Lane subnets."
}

output "control_security_group_id" {
  value       = module.network.control_security_group_id
  description = "Supervisor security group."
}

output "approved_guest_ami_id" {
  value       = var.approved_guest_ami_id
  description = "Reviewed AMI used by the disposable worker lanes."
}

output "guest_instance_type" {
  value       = var.guest_instance_type
  description = "Approved disposable worker guest class."
}

output "repository_urls" {
  value       = module.registry.repository_urls
  description = "Registry repositories."
}

output "cdn_domain_name" {
  value       = module.public_delivery.distribution_domain_name
  description = "CDN hostname."
}

output "cdn_distribution_id" {
  value       = module.public_delivery.distribution_id
  description = "CDN distribution."
}

output "alert_topic_arn" {
  value       = module.telemetry.alert_topic_arn
  description = "Alert topic."
}

output "backup_vault_name" {
  value       = module.backup.vault_name
  description = "Backup vault."
}

output "hardware_class" {
  value       = module.performance.hardware_class
  description = "Performance hardware class."
}

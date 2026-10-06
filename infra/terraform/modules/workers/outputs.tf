output "launch_template_arns" {
  value       = module.lanes.sandbox_launch_template_arns
  description = "Launch template ARN per lane."
}

output "launch_template_ids" {
  value       = module.lanes.sandbox_launch_template_ids
  description = "Launch template ID per lane (Ec2VmSandboxProvider configuration)."
}

output "launch_template_versions" {
  value       = module.lanes.sandbox_launch_template_versions
  description = "Default launch template version per execution lane; pin this version in the environment manifest."
}

output "guest_security_group_ids" {
  value       = module.lanes.sandbox_security_group_ids
  description = "Guest security group per lane."
}

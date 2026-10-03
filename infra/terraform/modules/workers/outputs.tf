output "launch_template_arns" {
  value       = module.lanes.sandbox_launch_template_arns
  description = "Launch template ARN per lane."
}

output "launch_template_ids" {
  value       = module.lanes.sandbox_launch_template_ids
  description = "Launch template ID per lane (Ec2VmSandboxProvider configuration)."
}

output "guest_security_group_ids" {
  value       = module.lanes.sandbox_security_group_ids
  description = "Guest security group per lane."
}

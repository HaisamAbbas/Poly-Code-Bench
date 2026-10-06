output "sandbox_launch_template_versions" {
  value       = { for lane, template in aws_launch_template.guest : lane => template.default_version }
  description = "Default lane launch template version; pin the observed version in the environment manifest."
}

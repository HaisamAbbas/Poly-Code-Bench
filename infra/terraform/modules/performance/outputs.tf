output "launch_template_arn" {
  value       = aws_launch_template.guest.arn
  description = "Performance lane launch template."
}

output "launch_template_id" {
  value       = aws_launch_template.guest.id
  description = "Performance lane launch template ID."
}

output "hardware_class" {
  value       = var.hardware_class
  description = "Hardware class recorded into performance evidence."
}

output "service_role_arns" {
  value       = { for role, value in aws_iam_role.service : role => value.arn }
  description = "Task role ARN per process role; config/environments/<env>.yaml must list exactly these."
}

output "operator_role_arns" {
  value       = { for role, value in aws_iam_role.operator : role => value.arn }
  description = "Operator role ARNs (MFA, named humans)."
}

output "task_execution_role_arn" {
  value       = aws_iam_role.task_execution.arn
  description = "ECS task execution role."
}

output "boundary_policy_arn" {
  value       = aws_iam_policy.boundary.arn
  description = "Permissions boundary shared by every role in this environment."
}

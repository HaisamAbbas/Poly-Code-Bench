output "alert_topic_arn" {
  value       = aws_sns_topic.alerts.arn
  description = "SNS topic for all required alerts."
}

output "prometheus_workspace_arn" {
  value       = aws_prometheus_workspace.this.arn
  description = "Managed Prometheus workspace."
}

output "prometheus_remote_write_url" {
  value       = "${aws_prometheus_workspace.this.prometheus_endpoint}api/v1/remote_write"
  description = "Remote-write endpoint for the OTel collector sidecars."
}

output "log_group_names" {
  value       = { for name, group in aws_cloudwatch_log_group.service : name => group.name }
  description = "Service log groups."
}

output "flow_log_group_arn" {
  value       = aws_cloudwatch_log_group.flow.arn
  description = "VPC flow log destination."
}

output "flow_log_role_arn" {
  value       = aws_iam_role.flow.arn
  description = "VPC flow log delivery role."
}

output "rds_monitoring_role_arn" {
  value       = aws_iam_role.rds_monitoring.arn
  description = "RDS enhanced monitoring role."
}

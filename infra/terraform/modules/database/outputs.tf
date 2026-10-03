output "endpoint" {
  value       = aws_db_instance.this.address
  description = "Database host for role credential secrets."
}

output "instance_arn" {
  value       = aws_db_instance.this.arn
  description = "Database instance ARN (backup plan selection, restore rehearsal source)."
}

output "instance_identifier" {
  value       = aws_db_instance.this.identifier
  description = "Source identifier for point-in-time restore."
}

output "security_group_id" {
  value       = aws_security_group.database.id
  description = "Database security group."
}

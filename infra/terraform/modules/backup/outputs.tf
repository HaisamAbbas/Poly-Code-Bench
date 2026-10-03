output "vault_name" {
  value       = aws_backup_vault.this.name
  description = "Backup vault used by the restore rehearsal."
}

output "plan_id" {
  value       = aws_backup_plan.this.id
  description = "Backup plan."
}

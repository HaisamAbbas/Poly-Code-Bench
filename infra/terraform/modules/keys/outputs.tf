output "key_arns" {
  value       = { for name, key in aws_kms_key.this : name => key.arn }
  description = "KMS key ARNs by purpose: data, hidden, secrets, signing, logs."
}

output "database_secret_arns" {
  value       = { for role, secret in aws_secretsmanager_secret.database : role => secret.arn }
  description = "Role-specific database credential secret ARNs (PCB_DATABASE_DSN_REF values)."
}

output "cursor_secret_arn" {
  value       = aws_secretsmanager_secret.cursor.arn
  description = "API cursor signing key secret."
}

output "signing_secret_arns" {
  value       = { for id, secret in aws_secretsmanager_secret.signing : id => secret.arn }
  description = "Signing key secrets by key ID (PCB_SIGNING_KEY_REF values)."
}

output "model_secret_namespace" {
  value       = "pcb/${var.environment}/model/"
  description = "Prefix resolved only inside the model gateway."
}

output "judge_secret_namespace" {
  value       = "pcb/${var.environment}/judge/"
  description = "Prefix resolved only inside the judge gateway."
}

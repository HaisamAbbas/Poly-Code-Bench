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

output "sandbox_control_identity_secret_arns" {
  value       = { for role, secret in aws_secretsmanager_secret.sandbox_control_identity : role => secret.arn }
  description = "Role-specific supervisor SSH identities; install private values out of band."
}

output "api_identity_secret_arn" {
  value       = aws_secretsmanager_secret.api_identity.arn
  description = "API identity-fingerprint export injected as PCB_API_IDENTITY_JSON."
}

output "web_auth_signing_secret_arn" {
  value       = aws_secretsmanager_secret.web_auth_signing.arn
  description = "Shared web/API HMAC signing-key reference; its value is set out of band."
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

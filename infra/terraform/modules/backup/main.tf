# Independent backup copies of the database and internal evidence (T 22.6, A 15.4).
#
# RDS PITR covers the trusted database; this plan adds daily vault copies of the database and
# of the internal evidence bucket so that "a database restored without its referenced
# artifacts" cannot happen: both are recovered to the same recovery point by the restore
# runbook. The hidden bucket is deliberately excluded (versioning + KMS + object holds only),
# because a backup role able to read hidden bundles would widen their audience.

locals {
  name = "pcb-${var.environment}"
  tags = merge(var.tags, { "pcb:environment" = var.environment })
}

resource "aws_backup_vault" "this" {
  name          = "${local.name}-vault"
  kms_key_arn   = var.data_key_arn
  force_destroy = false
  tags          = local.tags
}

resource "aws_backup_vault_lock_configuration" "this" {
  count               = var.vault_lock ? 1 : 0
  backup_vault_name   = aws_backup_vault.this.name
  min_retention_days  = 7
  max_retention_days  = var.retention_days
  changeable_for_days = 3
}

data "aws_iam_policy_document" "trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["backup.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "backup" {
  name               = "${local.name}-backup-service"
  assume_role_policy = data.aws_iam_policy_document.trust.json
  tags               = merge(local.tags, { "pcb:role" = "backup-service" })
}

resource "aws_iam_role_policy_attachment" "backup" {
  for_each = toset([
    "arn:aws:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForBackup",
    "arn:aws:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForRestores",
    "arn:aws:iam::aws:policy/AWSBackupServiceRolePolicyForS3Backup",
    "arn:aws:iam::aws:policy/AWSBackupServiceRolePolicyForS3Restore",
  ])
  role       = aws_iam_role.backup.name
  policy_arn = each.value
}

resource "aws_backup_plan" "this" {
  name = "${local.name}-evidence"

  rule {
    rule_name         = "daily"
    target_vault_name = aws_backup_vault.this.name
    schedule          = "cron(0 2 * * ? *)"
    start_window      = 60
    completion_window = 360
    lifecycle {
      delete_after = var.retention_days
    }
    recovery_point_tags = local.tags
  }

  rule {
    rule_name                = "continuous"
    target_vault_name        = aws_backup_vault.this.name
    schedule                 = "cron(0 * * * ? *)"
    enable_continuous_backup = true
    lifecycle {
      delete_after = 35
    }
    recovery_point_tags = local.tags
  }

  tags = local.tags
}

resource "aws_backup_selection" "this" {
  name         = "${local.name}-evidence"
  plan_id      = aws_backup_plan.this.id
  iam_role_arn = aws_iam_role.backup.arn
  resources    = concat([var.database_arn], var.bucket_arns)
}

resource "aws_backup_vault_notifications" "this" {
  backup_vault_name   = aws_backup_vault.this.name
  sns_topic_arn       = var.alert_topic_arn
  backup_vault_events = ["BACKUP_JOB_FAILED", "RESTORE_JOB_FAILED", "COPY_JOB_FAILED", "S3_BACKUP_OBJECT_FAILED", "S3_RESTORE_OBJECT_FAILED"]
}

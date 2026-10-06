# Encryption keys, secret containers and the publication signing key for one environment.
#
# Key policies enforce identity, not request strings: every key denies use to any principal
# whose IAM tag `pcb:environment` differs from this environment, and the hidden-bundle and
# signing keys additionally deny every principal whose `pcb:role` tag is not allowlisted.
# Credential and cryptographic secret VALUES are never managed here; operators write them out
# of band (runbook docs/operations/runbooks/signing-key-rotation.md). The API identity export's
# initial version is a non-sensitive empty schema document; no bearer or key material is stored.

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  name    = "pcb-${var.environment}"
  account = data.aws_caller_identity.current.account_id
  root    = "arn:${data.aws_partition.current.partition}:iam::${local.account}:root"
  tags    = merge(var.tags, { "pcb:environment" = var.environment })

  # Which application roles may use which key. Break-glass and Terraform admin principals
  # are carried by `admin_role_arns` and may administer but not use the hidden/signing keys.
  key_users = {
    data    = ["api", "scheduler", "model-gateway", "judge-gateway", "solve-supervisor", "eval-supervisor", "scorer", "publisher", "web", "migrator", "restore-operator", "ops-reaper"]
    hidden  = ["eval-supervisor", "admission-operator"]
    secrets = ["api", "scheduler", "model-gateway", "judge-gateway", "solve-supervisor", "eval-supervisor", "admission-operator", "scorer", "publisher", "web", "migrator", "restore-operator", "ops-reaper"]
    signing = ["publisher"]
    logs    = []
  }
}

data "aws_iam_policy_document" "key" {
  for_each = local.key_users

  statement {
    sid       = "AccountAdministersThroughIam"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = [local.root]
    }
  }

  statement {
    sid    = "DenyOtherEnvironments"
    effect = "Deny"
    actions = [
      "kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey*", "kms:ReEncrypt*", "kms:Sign", "kms:CreateGrant",
    ]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["*"]
    }
    # Every PolyCodeBench role carries its environment tag (modules/identity forbids changing
    # it), so a staging role is refused production keys. Untagged AWS service-linked roles
    # (RDS, Backup) are still governed by IAM grants and are not matched here.
    condition {
      test     = "StringNotEqualsIfExists"
      variable = "aws:PrincipalTag/pcb:environment"
      values   = [var.environment]
    }
  }

  dynamic "statement" {
    for_each = contains(["hidden", "signing"], each.key) ? [each.key] : []
    content {
      sid    = "DenyRolesOutsideAllowlist"
      effect = "Deny"
      actions = [
        "kms:Decrypt", "kms:GenerateDataKey*", "kms:ReEncrypt*", "kms:CreateGrant",
      ]
      resources = ["*"]
      principals {
        type        = "AWS"
        identifiers = ["*"]
      }
      condition {
        test     = "StringNotEquals"
        variable = "aws:PrincipalTag/pcb:role"
        values   = local.key_users[statement.value]
      }
      condition {
        test     = "Bool"
        variable = "aws:PrincipalIsAWSService"
        values   = ["false"]
      }
    }
  }

  dynamic "statement" {
    for_each = each.key == "logs" ? [1] : []
    content {
      sid       = "AuditAndAlertServicesEncrypt"
      actions   = ["kms:GenerateDataKey*", "kms:Decrypt", "kms:DescribeKey"]
      resources = ["*"]
      principals {
        type        = "Service"
        identifiers = ["cloudtrail.amazonaws.com", "cloudwatch.amazonaws.com", "aps.amazonaws.com", "backup.amazonaws.com", "sns.amazonaws.com", "budgets.amazonaws.com"]
      }
      condition {
        test     = "StringEquals"
        variable = "aws:SourceAccount"
        values   = [local.account]
      }
    }
  }

  dynamic "statement" {
    for_each = each.key == "logs" ? [1] : []
    content {
      sid       = "CloudWatchLogsEncrypts"
      actions   = ["kms:Encrypt*", "kms:Decrypt*", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:Describe*"]
      resources = ["*"]
      principals {
        type        = "Service"
        identifiers = ["logs.${var.region}.amazonaws.com"]
      }
      condition {
        test     = "ArnLike"
        variable = "kms:EncryptionContext:aws:logs:arn"
        values   = ["arn:${data.aws_partition.current.partition}:logs:${var.region}:${local.account}:log-group:/pcb/${var.environment}/*"]
      }
    }
  }
}

resource "aws_kms_key" "this" {
  for_each                = local.key_users
  description             = "PolyCodeBench ${var.environment} ${each.key} key"
  enable_key_rotation     = true
  rotation_period_in_days = 365
  deletion_window_in_days = 30
  policy                  = data.aws_iam_policy_document.key[each.key].json
  tags                    = merge(local.tags, { "pcb:key" = each.key })
}

resource "aws_kms_alias" "this" {
  for_each      = local.key_users
  name          = "alias/${local.name}-${each.key}"
  target_key_id = aws_kms_key.this[each.key].key_id
}

# Secret containers. Names form the namespaces resolved by the gateways (T 22.2):
#   pcb/<env>/db/<role>        role-specific database credentials
#   pcb/<env>/model/<config>   model provider credentials (model gateway only)
#   pcb/<env>/judge/<config>   judge credentials (judge gateway only)
#   pcb/<env>/signing/<key-id> Ed25519 publication signing key (publisher only)
resource "aws_secretsmanager_secret" "database" {
  for_each                = toset(var.database_roles)
  name                    = "pcb/${var.environment}/db/${each.key}"
  description             = "PostgreSQL credentials for the ${each.key} role (${var.environment})"
  kms_key_id              = aws_kms_key.this["secrets"].arn
  recovery_window_in_days = 30
  tags                    = merge(local.tags, { "pcb:secret-class" = "database", "pcb:role" = each.key })
}

resource "aws_secretsmanager_secret" "cursor" {
  name                    = "pcb/${var.environment}/api/cursor-signing-key"
  description             = "Public API pagination cursor HMAC key (32 bytes, hex)"
  kms_key_id              = aws_kms_key.this["secrets"].arn
  recovery_window_in_days = 30
  tags                    = merge(local.tags, { "pcb:secret-class" = "api" })
}

# Each supervisor has a separate SSH client key for its forced-command guest channel.
# Values are installed out of band; private key bytes are never written to Terraform state.
resource "aws_secretsmanager_secret" "sandbox_control_identity" {
  for_each                = toset(["solve-supervisor", "eval-supervisor", "admission-operator"])
  name                    = "pcb/${var.environment}/sandbox/control-identity-${each.key}"
  description             = "Forced-command SSH identity for ${each.key} disposable guests"
  kms_key_id              = aws_kms_key.this["secrets"].arn
  recovery_window_in_days = 30
  tags                    = merge(local.tags, { "pcb:secret-class" = "sandbox-control", "pcb:role" = each.key })
}

# The empty identity export makes OIDC-only submitter auth deployable without a file mount.
# Operators add hashed reviewer/admin token fingerprints out of band when those roles are enabled.
resource "aws_secretsmanager_secret" "api_identity" {
  name                    = "pcb/${var.environment}/api/identity-export"
  description             = "Hashed API operator token identities; contains no bearer token values"
  kms_key_id              = aws_kms_key.this["secrets"].arn
  recovery_window_in_days = 30
  tags                    = merge(local.tags, { "pcb:secret-class" = "api" })
}

resource "aws_secretsmanager_secret_version" "api_identity" {
  secret_id     = aws_secretsmanager_secret.api_identity.id
  secret_string = jsonencode({ schema_version = 1, principals = [] })
}

# The Next.js BFF and API share this HMAC key. Terraform creates only its reference;
# an operator installs a randomly generated value before any public task is started.
resource "aws_secretsmanager_secret" "web_auth_signing" {
  name                    = "pcb/${var.environment}/api/web-auth-signing-key"
  description             = "Shared HMAC key for short-lived web-to-API submitter assertions"
  kms_key_id              = aws_kms_key.this["secrets"].arn
  recovery_window_in_days = 30
  tags                    = merge(local.tags, { "pcb:secret-class" = "api" })
}

resource "aws_secretsmanager_secret" "signing" {
  for_each                = toset(var.signing_key_ids)
  name                    = "pcb/${var.environment}/signing/${each.key}"
  description             = "Ed25519 publication signing key ${each.key}; private bytes only, publisher-only"
  kms_key_id              = aws_kms_key.this["signing"].arn
  recovery_window_in_days = 30
  tags                    = merge(local.tags, { "pcb:secret-class" = "signing", "pcb:signing-key-id" = each.key })
}

data "aws_iam_policy_document" "signing_secret" {
  for_each = toset(var.signing_key_ids)
  statement {
    sid       = "PublisherOnly"
    effect    = "Deny"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["*"]
    }
    condition {
      test     = "StringNotEquals"
      variable = "aws:PrincipalTag/pcb:role"
      values   = ["publisher"]
    }
  }
  statement {
    sid       = "SameEnvironmentOnly"
    effect    = "Deny"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["*"]
    }
    condition {
      test     = "StringNotEquals"
      variable = "aws:PrincipalTag/pcb:environment"
      values   = [var.environment]
    }
  }
}

resource "aws_secretsmanager_secret_policy" "signing" {
  for_each   = toset(var.signing_key_ids)
  secret_arn = aws_secretsmanager_secret.signing[each.key].arn
  policy     = data.aws_iam_policy_document.signing_secret[each.key].json
}

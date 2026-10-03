# Service identities for one environment (T 10.2, 22.1, 22.3).
#
# Every application role carries two IAM tags that the rest of the stack trusts:
#   pcb:environment  - staging | production | integration
#   pcb:role         - the process role (matches polycodebench_configuration.ProcessRole)
# KMS key policies, bucket policies and the application identity guard
# (polycodebench_operations.identity) read these tags or the role ARN; none trusts a
# request field. A shared permissions boundary makes the tags immutable from inside the
# environment and denies any action on a resource tagged for another environment.

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  name      = "pcb-${var.environment}"
  account   = data.aws_caller_identity.current.account_id
  partition = data.aws_partition.current.partition
  tags      = merge(var.tags, { "pcb:environment" = var.environment })
  arn       = "arn:${local.partition}"

  secret_prefix = "${local.arn}:secretsmanager:${var.region}:${local.account}:secret:pcb/${var.environment}"
  log_prefix    = "${local.arn}:logs:${var.region}:${local.account}:log-group:/pcb/${var.environment}"
  instance_arn  = "${local.arn}:ec2:${var.region}:${local.account}:instance/*"

  # Process roles run as ECS tasks; operator roles are assumed by named humans through SSO.
  service_roles  = ["api", "scheduler", "model-gateway", "judge-gateway", "solve-supervisor", "eval-supervisor", "scorer", "publisher", "web", "migrator", "ops-reaper", "admission-operator", "restore-operator"]
  operator_roles = ["oncall-operator", "release-approver"]

  lane_for_role = {
    "solve-supervisor"   = ["solve"]
    "eval-supervisor"    = ["grading", "performance"]
    "admission-operator" = ["admission"]
  }

  db_secret_roles = ["api", "scheduler", "solve-supervisor", "eval-supervisor", "scorer", "publisher", "migrator", "restore-operator", "ops-reaper"]
}

# ---------------------------------------------------------------- permissions boundary
data "aws_iam_policy_document" "boundary" {
  statement {
    sid       = "Ceiling"
    actions   = ["*"]
    resources = ["*"]
  }

  statement {
    sid       = "NoCrossEnvironmentResources"
    effect    = "Deny"
    actions   = ["*"]
    resources = ["*"]
    condition {
      test     = "StringNotEqualsIfExists"
      variable = "aws:ResourceTag/pcb:environment"
      values   = [var.environment]
    }
  }

  statement {
    sid    = "IdentityIsImmutableFromInside"
    effect = "Deny"
    actions = [
      "iam:*", "sts:TagSession", "organizations:*", "account:*",
      "kms:CreateKey", "kms:PutKeyPolicy", "kms:ScheduleKeyDeletion", "kms:DisableKey",
      "s3:PutBucketPolicy", "s3:DeleteBucketPolicy", "s3:PutBucketPublicAccessBlock",
      "s3:PutLifecycleConfiguration", "s3:PutBucketVersioning", "s3:PutObjectLegalHold",
      "secretsmanager:PutResourcePolicy", "secretsmanager:DeleteResourcePolicy",
      "cloudtrail:StopLogging", "cloudtrail:DeleteTrail", "cloudtrail:UpdateTrail",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "LaunchOnlyTaggedForThisEnvironment"
    effect    = "Deny"
    actions   = ["ec2:RunInstances", "ec2:CreateVolume"]
    resources = ["${local.arn}:ec2:*:*:instance/*", "${local.arn}:ec2:*:*:volume/*"]
    condition {
      test     = "StringNotEquals"
      variable = "aws:RequestTag/pcb:environment"
      values   = [var.environment]
    }
  }

  statement {
    sid       = "NoRetaggingOutsideLaunch"
    effect    = "Deny"
    actions   = ["ec2:CreateTags", "ec2:DeleteTags"]
    resources = ["*"]
    condition {
      test     = "StringNotEquals"
      variable = "ec2:CreateAction"
      values   = ["RunInstances"]
    }
  }

  statement {
    sid       = "StayInRegion"
    effect    = "Deny"
    actions   = ["*"]
    resources = ["*"]
    condition {
      test     = "StringNotEquals"
      variable = "aws:RequestedRegion"
      values   = [var.region, "us-east-1"] # us-east-1 only for global CloudFront/IAM reads
    }
  }
}

resource "aws_iam_policy" "boundary" {
  name        = "${local.name}-boundary"
  description = "Ceiling for every PolyCodeBench ${var.environment} role"
  policy      = data.aws_iam_policy_document.boundary.json
  tags        = local.tags
}

# ---------------------------------------------------------------- trust
data "aws_iam_policy_document" "ecs_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account]
    }
  }
}

data "aws_iam_policy_document" "operator_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "AWS"
      identifiers = var.operator_principal_arns
    }
    condition {
      test     = "Bool"
      variable = "aws:MultiFactorAuthPresent"
      values   = ["true"]
    }
  }
}

resource "aws_iam_role" "service" {
  for_each             = toset(local.service_roles)
  name                 = "${local.name}-${each.key}"
  assume_role_policy   = data.aws_iam_policy_document.ecs_trust.json
  permissions_boundary = aws_iam_policy.boundary.arn
  max_session_duration = 3600
  tags                 = merge(local.tags, { "pcb:role" = each.key })
}

resource "aws_iam_role" "operator" {
  for_each             = toset(local.operator_roles)
  name                 = "${local.name}-${each.key}"
  assume_role_policy   = data.aws_iam_policy_document.operator_trust.json
  permissions_boundary = aws_iam_policy.boundary.arn
  max_session_duration = 3600
  tags                 = merge(local.tags, { "pcb:role" = each.key })
}

# Task execution role: pulls digest-pinned images and injects the role's secrets at start.
resource "aws_iam_role" "task_execution" {
  name                 = "${local.name}-task-execution"
  assume_role_policy   = data.aws_iam_policy_document.ecs_trust.json
  permissions_boundary = aws_iam_policy.boundary.arn
  tags                 = merge(local.tags, { "pcb:role" = "task-execution" })
}

data "aws_iam_policy_document" "task_execution" {
  statement {
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    actions   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"]
    resources = var.ecr_repository_arns
  }
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${local.log_prefix}/*"]
  }
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = ["${local.secret_prefix}/db/*", "${local.secret_prefix}/api/*"]
  }
  statement {
    actions   = ["kms:Decrypt"]
    resources = [var.key_arns["secrets"]]
  }
}

resource "aws_iam_role_policy" "task_execution" {
  name   = "execution"
  role   = aws_iam_role.task_execution.id
  policy = data.aws_iam_policy_document.task_execution.json
}

# ---------------------------------------------------------------- per-role permissions
data "aws_iam_policy_document" "service" {
  for_each = toset(local.service_roles)

  statement {
    sid       = "Telemetry"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents", "cloudwatch:PutMetricData", "sts:GetCallerIdentity"]
    resources = ["*"]
  }

  statement {
    sid       = "PrometheusRemoteWrite"
    actions   = ["aps:RemoteWrite"]
    resources = [var.prometheus_workspace_arn]
  }

  dynamic "statement" {
    for_each = contains(local.db_secret_roles, each.key) ? [each.key] : []
    content {
      sid       = "OwnDatabaseCredential"
      actions   = ["secretsmanager:GetSecretValue"]
      resources = ["${local.secret_prefix}/db/${statement.value}-*"]
    }
  }

  dynamic "statement" {
    for_each = contains(local.db_secret_roles, each.key) || contains(["model-gateway", "judge-gateway"], each.key) ? [1] : []
    content {
      sid       = "SecretsKey"
      actions   = ["kms:Decrypt"]
      resources = [var.key_arns["secrets"]]
    }
  }

  dynamic "statement" {
    for_each = each.key == "model-gateway" ? [1] : []
    content {
      sid       = "ModelNamespaceOnly"
      actions   = ["secretsmanager:GetSecretValue"]
      resources = ["${local.secret_prefix}/model/*"]
    }
  }

  dynamic "statement" {
    for_each = each.key == "judge-gateway" ? [1] : []
    content {
      sid       = "JudgeNamespaceOnly"
      actions   = ["secretsmanager:GetSecretValue"]
      resources = ["${local.secret_prefix}/judge/*"]
    }
  }

  dynamic "statement" {
    for_each = each.key == "api" ? [1] : []
    content {
      sid       = "CursorKey"
      actions   = ["secretsmanager:GetSecretValue"]
      resources = ["${local.secret_prefix}/api/*"]
    }
  }

  # Internal evidence: stage inputs/outputs, archives and scorecards.
  dynamic "statement" {
    for_each = contains(["solve-supervisor", "eval-supervisor", "admission-operator", "scorer", "publisher", "restore-operator", "scheduler"], each.key) ? [1] : []
    content {
      sid       = "InternalEvidenceRead"
      actions   = ["s3:GetObject", "s3:GetObjectVersion", "s3:ListBucket"]
      resources = [var.bucket_arns["internal"], "${var.bucket_arns["internal"]}/*"]
    }
  }

  dynamic "statement" {
    for_each = contains(["solve-supervisor", "eval-supervisor", "admission-operator", "scorer"], each.key) ? [1] : []
    content {
      sid       = "InternalEvidenceWrite"
      actions   = ["s3:PutObject"]
      resources = ["${var.bucket_arns["internal"]}/*"]
    }
  }

  dynamic "statement" {
    for_each = contains(["solve-supervisor", "eval-supervisor", "admission-operator", "scorer", "publisher", "restore-operator", "scheduler"], each.key) ? [1] : []
    content {
      sid       = "InternalKey"
      actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
      resources = [var.key_arns["data"]]
    }
  }

  # Hidden bundles: grading/admission lanes only. The bucket and key policies repeat this.
  dynamic "statement" {
    for_each = contains(["eval-supervisor", "admission-operator"], each.key) ? [1] : []
    content {
      sid       = "HiddenBundlesRead"
      actions   = ["s3:GetObject", "s3:GetObjectVersion"]
      resources = ["${var.bucket_arns["hidden"]}/*"]
    }
  }

  dynamic "statement" {
    for_each = contains(["eval-supervisor", "admission-operator"], each.key) ? [1] : []
    content {
      sid       = "HiddenKey"
      actions   = ["kms:Decrypt"]
      resources = [var.key_arns["hidden"]]
    }
  }

  # Disposable guests: launch only from this lane's template with this lane's tags.
  dynamic "statement" {
    for_each = contains(keys(local.lane_for_role), each.key) ? [each.key] : []
    content {
      sid       = "LaunchOwnLaneGuests"
      actions   = ["ec2:RunInstances"]
      resources = ["*"]
      condition {
        test     = "ArnLike"
        variable = "ec2:LaunchTemplate"
        values   = [for lane in local.lane_for_role[statement.value] : var.launch_template_arns[lane]]
      }
    }
  }

  dynamic "statement" {
    for_each = contains(keys(local.lane_for_role), each.key) ? [each.key] : []
    content {
      sid       = "TagAtLaunchWithOwnLane"
      actions   = ["ec2:CreateTags"]
      resources = ["*"]
      condition {
        test     = "StringEquals"
        variable = "ec2:CreateAction"
        values   = ["RunInstances"]
      }
      condition {
        test     = "StringEquals"
        variable = "aws:RequestTag/pcb:lane"
        values   = local.lane_for_role[statement.value]
      }
    }
  }

  dynamic "statement" {
    for_each = contains(keys(local.lane_for_role), each.key) || each.key == "ops-reaper" ? [each.key] : []
    content {
      sid       = "DescribeGuests"
      actions   = ["ec2:DescribeInstances", "ec2:DescribeInstanceStatus", "ec2:DescribeTags"]
      resources = ["*"]
    }
  }

  dynamic "statement" {
    for_each = contains(keys(local.lane_for_role), each.key) ? [each.key] : []
    content {
      sid       = "TerminateOwnLaneGuests"
      actions   = ["ec2:TerminateInstances"]
      resources = [local.instance_arn]
      condition {
        test     = "StringEquals"
        variable = "aws:ResourceTag/pcb:lane"
        values   = local.lane_for_role[statement.value]
      }
      condition {
        test     = "StringEquals"
        variable = "aws:ResourceTag/pcb:owner"
        values   = ["polycodebench"]
      }
    }
  }

  # The reaper may terminate any PolyCodeBench guest of this environment; the code decides
  # expiry from pcb:expires-at (TTL + 10 minute alert threshold, T 22.5).
  dynamic "statement" {
    for_each = each.key == "ops-reaper" ? [1] : []
    content {
      sid       = "ReapExpiredGuests"
      actions   = ["ec2:TerminateInstances"]
      resources = [local.instance_arn]
      condition {
        test     = "StringEquals"
        variable = "aws:ResourceTag/pcb:owner"
        values   = ["polycodebench"]
      }
    }
  }

  dynamic "statement" {
    for_each = each.key == "publisher" ? [1] : []
    content {
      sid       = "PublicProjectionWrite"
      actions   = ["s3:PutObject", "s3:GetObject", "s3:ListBucket"]
      resources = [var.bucket_arns["public"], "${var.bucket_arns["public"]}/*"]
    }
  }

  dynamic "statement" {
    for_each = each.key == "publisher" ? [1] : []
    content {
      sid       = "SigningKeyOnly"
      actions   = ["secretsmanager:GetSecretValue", "kms:Decrypt"]
      resources = ["${local.secret_prefix}/signing/*", var.key_arns["signing"]]
    }
  }

  dynamic "statement" {
    for_each = each.key == "publisher" ? [1] : []
    content {
      sid       = "CdnInvalidate"
      actions   = ["cloudfront:CreateInvalidation"]
      resources = ["*"]
    }
  }

  # Restore rehearsal: may create and delete only instances it tags as rehearsal targets.
  dynamic "statement" {
    for_each = each.key == "restore-operator" ? [1] : []
    content {
      sid       = "RestoreToTaggedRehearsalInstance"
      actions   = ["rds:RestoreDBInstanceToPointInTime", "rds:RestoreDBInstanceFromDBSnapshot", "rds:AddTagsToResource"]
      resources = ["*"]
      condition {
        test     = "StringEquals"
        variable = "aws:RequestTag/pcb:purpose"
        values   = ["restore-rehearsal"]
      }
    }
  }

  dynamic "statement" {
    for_each = each.key == "restore-operator" ? [1] : []
    content {
      sid       = "DeleteOnlyRehearsalInstances"
      actions   = ["rds:DeleteDBInstance"]
      resources = ["*"]
      condition {
        test     = "StringEquals"
        variable = "aws:ResourceTag/pcb:purpose"
        values   = ["restore-rehearsal"]
      }
    }
  }

  dynamic "statement" {
    for_each = each.key == "restore-operator" ? [1] : []
    content {
      sid       = "InspectBackups"
      actions   = ["rds:Describe*", "backup:List*", "backup:Describe*", "backup:StartRestoreJob", "backup:GetRecoveryPointRestoreMetadata"]
      resources = ["*"]
    }
  }
}

resource "aws_iam_role_policy" "service" {
  for_each = toset(local.service_roles)
  name     = "${each.key}-permissions"
  role     = aws_iam_role.service[each.key].id
  policy   = data.aws_iam_policy_document.service[each.key].json
}

# Operators get read-only diagnosis plus the drill actions their runbooks name.
data "aws_iam_policy_document" "operator" {
  for_each = toset(local.operator_roles)

  statement {
    sid = "Diagnose"
    actions = [
      "ecs:Describe*", "ecs:List*", "logs:StartQuery", "logs:GetQueryResults", "logs:FilterLogEvents",
      "cloudwatch:GetMetricData", "cloudwatch:DescribeAlarms", "ec2:DescribeInstances", "rds:Describe*",
      "aps:QueryMetrics", "aps:GetSeries", "aps:GetLabels",
    ]
    resources = ["*"]
  }

  dynamic "statement" {
    for_each = each.key == "oncall-operator" ? [1] : []
    content {
      sid       = "RunbookTasks"
      actions   = ["ecs:RunTask", "ecs:UpdateService", "iam:PassRole"]
      resources = ["*"]
      condition {
        test     = "StringEquals"
        variable = "aws:ResourceTag/pcb:environment"
        values   = [var.environment]
      }
    }
  }
}

resource "aws_iam_role_policy" "operator" {
  for_each = toset(local.operator_roles)
  name     = "${each.key}-permissions"
  role     = aws_iam_role.operator[each.key].id
  policy   = data.aws_iam_policy_document.operator[each.key].json
}

# Telemetry and required alerts (T 22.5).
#
# Single source of truth for alert rules: infra/observability/prometheus/alerts.yaml.
# The same file is checked by `promtool check rules` / `promtool test rules` in CI and loaded
# here into Amazon Managed Service for Prometheus. The one alert that cannot come from
# application metrics - unexpected hidden-artifact access - is derived from CloudTrail S3
# data events so a compromised service cannot suppress it by not reporting.

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  name = "pcb-${var.environment}"
  tags = merge(var.tags, { "pcb:environment" = var.environment })
}

resource "aws_cloudwatch_log_group" "service" {
  for_each          = toset(var.services)
  name              = "/pcb/${var.environment}/${each.key}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.logs_key_arn
  tags              = merge(local.tags, { "pcb:role" = each.key })
}

resource "aws_cloudwatch_log_group" "flow" {
  name              = "/pcb/${var.environment}/vpc-flow"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.logs_key_arn
  tags              = local.tags
}

data "aws_iam_policy_document" "flow_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["vpc-flow-logs.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "flow" {
  name               = "${local.name}-flow-logs"
  assume_role_policy = data.aws_iam_policy_document.flow_trust.json
  tags               = merge(local.tags, { "pcb:role" = "flow-logs" })
}

data "aws_iam_policy_document" "flow" {
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
    resources = ["${aws_cloudwatch_log_group.flow.arn}:*"]
  }
}

resource "aws_iam_role_policy" "flow" {
  name   = "deliver"
  role   = aws_iam_role.flow.id
  policy = data.aws_iam_policy_document.flow.json
}

data "aws_iam_policy_document" "rds_monitoring_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["monitoring.rds.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "rds_monitoring" {
  name               = "${local.name}-rds-monitoring"
  assume_role_policy = data.aws_iam_policy_document.rds_monitoring_trust.json
  tags               = merge(local.tags, { "pcb:role" = "rds-monitoring" })
}

resource "aws_iam_role_policy_attachment" "rds_monitoring" {
  role       = aws_iam_role.rds_monitoring.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
}

# ---------------------------------------------------------------- alert routing
resource "aws_sns_topic" "alerts" {
  name              = "${local.name}-alerts"
  kms_master_key_id = var.logs_key_arn
  tags              = local.tags
}

resource "aws_sns_topic_subscription" "oncall" {
  for_each  = toset(var.alert_email_endpoints)
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = each.value
}

data "aws_iam_policy_document" "alerts_topic" {
  statement {
    sid       = "AccountPublishes"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]
    principals {
      type        = "AWS"
      identifiers = ["arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
  }
  statement {
    sid       = "AlertingServicesPublish"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]
    principals {
      type        = "Service"
      identifiers = ["budgets.amazonaws.com", "cloudwatch.amazonaws.com", "aps.amazonaws.com", "backup.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_sns_topic_policy" "alerts" {
  arn    = aws_sns_topic.alerts.arn
  policy = data.aws_iam_policy_document.alerts_topic.json
}

# Hard spend guard for the environment's infrastructure (owner-approved cap, not a guess).
# Model/judge provider spend is governed separately by the gateway budget accounts.
resource "aws_budgets_budget" "monthly" {
  name         = "${local.name}-infrastructure"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "TagKeyValue"
    values = [format("user:pcb:environment$%s", var.environment)] # requires the pcb:environment cost-allocation tag to be activated
  }

  dynamic "notification" {
    for_each = { actual50 = ["ACTUAL", 50], actual80 = ["ACTUAL", 80], actual100 = ["ACTUAL", 100], forecast100 = ["FORECASTED", 100] }
    content {
      comparison_operator       = "GREATER_THAN"
      threshold                 = notification.value[1]
      threshold_type            = "PERCENTAGE"
      notification_type         = notification.value[0]
      subscriber_sns_topic_arns = [aws_sns_topic.alerts.arn]
    }
  }
}

# ---------------------------------------------------------------- metrics + rules
resource "aws_prometheus_workspace" "this" {
  alias = local.name
  logging_configuration {
    log_group_arn = "${aws_cloudwatch_log_group.service["prometheus"].arn}:*"
  }
  tags = local.tags
}

resource "aws_prometheus_rule_group_namespace" "required" {
  name         = "pcb-required-alerts"
  workspace_id = aws_prometheus_workspace.this.id
  data         = file(var.alert_rules_path)
}

resource "aws_prometheus_alert_manager_definition" "this" {
  workspace_id = aws_prometheus_workspace.this.id
  definition   = <<-EOT
    alertmanager_config: |
      route:
        receiver: oncall
        group_by: ['alertname', 'environment']
        group_wait: 30s
        repeat_interval: 4h
      receivers:
        - name: oncall
          sns_configs:
            - topic_arn: ${aws_sns_topic.alerts.arn}
              sigv4:
                region: ${var.region}
              attributes:
                environment: ${var.environment}
  EOT
}

# ---------------------------------------------------------------- hidden-access audit trail
resource "aws_s3_bucket" "trail" {
  bucket        = "${var.bucket_prefix}-${var.environment}-trail"
  force_destroy = false
  tags          = merge(local.tags, { "pcb:bucket-class" = "audit-trail" })
}

resource "aws_s3_bucket_public_access_block" "trail" {
  bucket                  = aws_s3_bucket.trail.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "trail" {
  bucket = aws_s3_bucket.trail.id
  versioning_configuration {
    status = "Enabled"
  }
}

data "aws_iam_policy_document" "trail_bucket" {
  statement {
    sid       = "CloudTrailAclCheck"
    actions   = ["s3:GetBucketAcl"]
    resources = [aws_s3_bucket.trail.arn]
    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }
  }
  statement {
    sid       = "CloudTrailWrite"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.trail.arn}/AWSLogs/${data.aws_caller_identity.current.account_id}/*"]
    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "s3:x-amz-acl"
      values   = ["bucket-owner-full-control"]
    }
  }
}

resource "aws_s3_bucket_policy" "trail" {
  bucket = aws_s3_bucket.trail.id
  policy = data.aws_iam_policy_document.trail_bucket.json
}

resource "aws_cloudwatch_log_group" "trail" {
  name              = "/pcb/${var.environment}/cloudtrail"
  retention_in_days = 365
  kms_key_id        = var.logs_key_arn
  tags              = local.tags
}

data "aws_iam_policy_document" "trail_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "trail" {
  name               = "${local.name}-cloudtrail"
  assume_role_policy = data.aws_iam_policy_document.trail_trust.json
  tags               = merge(local.tags, { "pcb:role" = "cloudtrail" })
}

resource "aws_iam_role_policy" "trail" {
  name = "deliver"
  role = aws_iam_role.trail.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
      Resource = "${aws_cloudwatch_log_group.trail.arn}:*"
    }]
  })
}

resource "aws_cloudtrail" "this" {
  name                          = "${local.name}-audit"
  s3_bucket_name                = aws_s3_bucket.trail.id
  include_global_service_events = true
  is_multi_region_trail         = true
  enable_log_file_validation    = true
  kms_key_id                    = var.logs_key_arn
  cloud_watch_logs_group_arn    = "${aws_cloudwatch_log_group.trail.arn}:*"
  cloud_watch_logs_role_arn     = aws_iam_role.trail.arn

  advanced_event_selector {
    name = "Hidden bundle object reads and writes"
    field_selector {
      field  = "eventCategory"
      equals = ["Data"]
    }
    field_selector {
      field  = "resources.type"
      equals = ["AWS::S3::Object"]
    }
    field_selector {
      field       = "resources.ARN"
      starts_with = ["${var.hidden_bucket_arn}/"]
    }
  }

  advanced_event_selector {
    name = "Management events"
    field_selector {
      field  = "eventCategory"
      equals = ["Management"]
    }
  }

  depends_on = [aws_s3_bucket_policy.trail]
  tags       = local.tags
}

# Any hidden-bucket object read whose caller role is not an allowlisted hidden reader.
resource "aws_cloudwatch_log_metric_filter" "unexpected_hidden_access" {
  name           = "${local.name}-unexpected-hidden-access"
  log_group_name = aws_cloudwatch_log_group.trail.name
  pattern        = "{ ($.eventSource = \"s3.amazonaws.com\") && ($.eventName = \"GetObject\") && ($.requestParameters.bucketName = \"${var.hidden_bucket_name}\") && ($.userIdentity.sessionContext.sessionIssuer.userName != \"pcb-${var.environment}-eval-supervisor\") && ($.userIdentity.sessionContext.sessionIssuer.userName != \"pcb-${var.environment}-admission-operator\") }"
  metric_transformation {
    name      = "UnexpectedHiddenAccess"
    namespace = "PolyCodeBench/${var.environment}"
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "unexpected_hidden_access" {
  alarm_name          = "${local.name}-unexpected-hidden-access"
  alarm_description   = "Runbook: docs/operations/runbooks/held-out-leak-and-compromised-identity.md"
  namespace           = "PolyCodeBench/${var.environment}"
  metric_name         = "UnexpectedHiddenAccess"
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  tags                = local.tags
}

resource "aws_cloudwatch_metric_alarm" "database_storage" {
  alarm_name          = "${local.name}-database-free-storage-low"
  alarm_description   = "Runbook: docs/operations/runbooks/database-object-store-restore.md"
  namespace           = "AWS/RDS"
  metric_name         = "FreeStorageSpace"
  dimensions          = { DBInstanceIdentifier = "${local.name}-postgres" } # modules/database naming
  statistic           = "Minimum"
  period              = 300
  evaluation_periods  = 3
  threshold           = var.database_free_storage_alarm_bytes
  comparison_operator = "LessThanThreshold"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  tags                = local.tags
}

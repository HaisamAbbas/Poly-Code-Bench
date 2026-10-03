# Artifact buckets, encryption, lifecycle and access policy for one environment (T 6, 22.6).
#
#   hidden    - hidden grading bundles; readable only by eval-supervisor/admission-operator;
#               every read is a CloudTrail data event (modules/telemetry alerts on others).
#   internal  - task sources, stage inputs/outputs, evidence archives, scorecards.
#   public    - signed public projections; served only through CloudFront (OAC).
#   logs      - S3/CloudFront access logs.
# Lifecycle (T 22.6): provisional uploads and debug artifacts expire after 30 days,
# cancelled-run logs after 90 days. Expiry rules match only those prefixes; canonical evidence
# keys (<domain>/<aa>/<digest>) cannot use them (S3ArtifactStore reserves the names), and the
# audited hold workflow (ArtifactRepository.add_hold) records legal/rights/incident holds.
# Published release objects are protected by S3 Object Lock in governance mode.

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  name    = "pcb-${var.environment}"
  tags    = merge(var.tags, { "pcb:environment" = var.environment })
  buckets = toset(["hidden", "internal", "public", "logs"])
  kms_for = {
    hidden   = var.key_arns["hidden"]
    internal = var.key_arns["data"]
    public   = null # CloudFront OAC reads; SSE-S3 keeps the CDN path free of KMS grants.
    logs     = null # Log delivery services require SSE-S3.
  }
  readers = {
    hidden = ["eval-supervisor", "admission-operator"]
  }
}

resource "aws_s3_bucket" "this" {
  for_each            = local.buckets
  bucket              = "${var.bucket_prefix}-${var.environment}-${each.key}"
  object_lock_enabled = each.key == "public"
  force_destroy       = false
  tags                = merge(local.tags, { "pcb:bucket-class" = each.key })
}

resource "aws_s3_bucket_ownership_controls" "this" {
  for_each = local.buckets
  bucket   = aws_s3_bucket.this[each.key].id
  rule {
    object_ownership = each.key == "logs" ? "BucketOwnerPreferred" : "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each                = local.buckets
  bucket                  = aws_s3_bucket.this[each.key].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "this" {
  for_each = local.buckets
  bucket   = aws_s3_bucket.this[each.key].id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = local.buckets
  bucket   = aws_s3_bucket.this[each.key].id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = local.kms_for[each.key] == null ? "AES256" : "aws:kms"
      kms_master_key_id = local.kms_for[each.key]
    }
    bucket_key_enabled = local.kms_for[each.key] != null
  }
}

resource "aws_s3_bucket_object_lock_configuration" "public" {
  bucket = aws_s3_bucket.this["public"].id
  rule {
    default_retention {
      mode = "GOVERNANCE"
      days = var.published_object_lock_days
    }
  }
  depends_on = [aws_s3_bucket_versioning.this]
}

resource "aws_s3_bucket_lifecycle_configuration" "internal" {
  bucket = aws_s3_bucket.this["internal"].id

  rule {
    id     = "provisional-uploads-30d"
    status = "Enabled"
    filter {
      prefix = "provisional/"
    }
    expiration {
      days = var.provisional_retention_days
    }
  }

  rule {
    id     = "debug-artifacts-30d"
    status = "Enabled"
    filter {
      prefix = "debug/"
    }
    expiration {
      days = var.provisional_retention_days
    }
  }

  rule {
    id     = "cancelled-run-logs-90d"
    status = "Enabled"
    filter {
      prefix = "cancelled-logs/"
    }
    expiration {
      days = var.cancelled_log_retention_days
    }
  }

  rule {
    id     = "noncurrent-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = var.noncurrent_version_days
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 2
    }
  }

  depends_on = [aws_s3_bucket_versioning.this]
}

resource "aws_s3_bucket_lifecycle_configuration" "logs" {
  bucket = aws_s3_bucket.this["logs"].id
  rule {
    id     = "access-logs"
    status = "Enabled"
    filter {}
    expiration {
      days = var.access_log_retention_days
    }
    noncurrent_version_expiration {
      noncurrent_days = 7
    }
  }
  depends_on = [aws_s3_bucket_versioning.this]
}

resource "aws_s3_bucket_logging" "this" {
  for_each      = toset(["hidden", "internal", "public"])
  bucket        = aws_s3_bucket.this[each.key].id
  target_bucket = aws_s3_bucket.this["logs"].id
  target_prefix = "s3/${each.key}/"
}

# ---------------------------------------------------------------- bucket policies
data "aws_iam_policy_document" "bucket" {
  for_each = toset(["hidden", "internal", "public"])

  statement {
    sid       = "TlsOnly"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.this[each.key].arn, "${aws_s3_bucket.this[each.key].arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }

  statement {
    sid       = "SameEnvironmentPrincipalsOnly"
    effect    = "Deny"
    actions   = ["s3:GetObject*", "s3:PutObject*", "s3:DeleteObject*", "s3:ListBucket*"]
    resources = [aws_s3_bucket.this[each.key].arn, "${aws_s3_bucket.this[each.key].arn}/*"]
    principals {
      type        = "AWS"
      identifiers = ["*"]
    }
    condition {
      test     = "StringNotEqualsIfExists"
      variable = "aws:PrincipalTag/pcb:environment"
      values   = [var.environment]
    }
    condition {
      test     = "Bool"
      variable = "aws:PrincipalIsAWSService"
      values   = ["false"]
    }
  }

  dynamic "statement" {
    for_each = each.key == "hidden" ? [1] : []
    content {
      sid       = "HiddenReadersOnly"
      effect    = "Deny"
      actions   = ["s3:GetObject*"]
      resources = ["${aws_s3_bucket.this["hidden"].arn}/*"]
      principals {
        type        = "AWS"
        identifiers = ["*"]
      }
      condition {
        test     = "StringNotEquals"
        variable = "aws:PrincipalTag/pcb:role"
        values   = local.readers["hidden"]
      }
    }
  }

  # Evidence is immutable once written: no application role may delete or overwrite it.
  dynamic "statement" {
    for_each = each.key != "public" ? [1] : []
    content {
      sid       = "NoApplicationDeletes"
      effect    = "Deny"
      actions   = ["s3:DeleteObject", "s3:DeleteObjectVersion"]
      resources = ["${aws_s3_bucket.this[each.key].arn}/*"]
      principals {
        type        = "AWS"
        identifiers = ["*"]
      }
      condition {
        test     = "StringLike"
        variable = "aws:PrincipalTag/pcb:role"
        values   = ["*"]
      }
    }
  }

  dynamic "statement" {
    for_each = each.key == "public" ? [1] : []
    content {
      sid       = "PublisherOnlyWrites"
      effect    = "Deny"
      actions   = ["s3:PutObject*", "s3:DeleteObject*"]
      resources = ["${aws_s3_bucket.this["public"].arn}/*"]
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
  }
}

# The public bucket policy is attached by modules/public_delivery, which adds the CloudFront
# origin-read statement to `public_base_policy_json` (avoids a bucket <-> CDN cycle).
resource "aws_s3_bucket_policy" "this" {
  for_each   = toset(["hidden", "internal"])
  bucket     = aws_s3_bucket.this[each.key].id
  policy     = data.aws_iam_policy_document.bucket[each.key].json
  depends_on = [aws_s3_bucket_public_access_block.this]
}

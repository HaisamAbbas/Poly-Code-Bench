output "bucket_names" {
  value       = { for name, bucket in aws_s3_bucket.this : name => bucket.bucket }
  description = "Bucket names (PCB_BUCKET_HIDDEN/INTERNAL/PUBLIC)."
}

output "bucket_arns" {
  value       = { for name, bucket in aws_s3_bucket.this : name => bucket.arn }
  description = "Bucket ARNs."
}

output "public_bucket_regional_domain_name" {
  value       = aws_s3_bucket.this["public"].bucket_regional_domain_name
  description = "CloudFront origin domain for the public bucket."
}

output "logs_bucket_domain_name" {
  value       = aws_s3_bucket.this["logs"].bucket_domain_name
  description = "Access-log destination."
}

output "public_base_policy_json" {
  value       = data.aws_iam_policy_document.bucket["public"].json
  description = "TLS, same-environment and publisher-only statements for the public bucket."
}

output "public_bucket_id" {
  value       = aws_s3_bucket.this["public"].id
  description = "Public projection bucket."
}

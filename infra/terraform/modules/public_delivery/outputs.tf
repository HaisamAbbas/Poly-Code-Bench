output "distribution_id" {
  value       = aws_cloudfront_distribution.this.id
  description = "Distribution for publisher invalidations."
}

output "distribution_domain_name" {
  value       = aws_cloudfront_distribution.this.domain_name
  description = "CNAME target for the public hostnames."
}

# Public delivery: CDN in front of the signed public projection bucket and the read-only
# public API / web origin (A 15.1, T 19.5, T 21).
#
#   /releases/*  /keys/*   -> public bucket via Origin Access Control (immutable objects)
#   /v1/*  /healthz        -> ALB -> api (read-only public routes, short cache)
#   default                -> ALB -> web (Next.js)
# The bucket stays private (public access block on); only this distribution can read it.

locals {
  name = "pcb-${var.environment}"
  tags = merge(var.tags, { "pcb:environment" = var.environment })
}

data "aws_cloudfront_cache_policy" "optimized" {
  name = "Managed-CachingOptimized"
}

data "aws_cloudfront_cache_policy" "disabled" {
  name = "Managed-CachingDisabled"
}

data "aws_cloudfront_origin_request_policy" "all_viewer_except_host" {
  name = "Managed-AllViewerExceptHostHeader"
}

resource "aws_cloudfront_origin_access_control" "public" {
  name                              = "${local.name}-public-projections"
  description                       = "Signed public projections"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_cache_policy" "api" {
  name        = "${local.name}-public-api"
  comment     = "Public API: ETag revalidation, short TTL; release-pinned URLs are immutable upstream"
  default_ttl = 60
  max_ttl     = 300
  min_ttl     = 0
  parameters_in_cache_key_and_forwarded_to_origin {
    enable_accept_encoding_brotli = true
    enable_accept_encoding_gzip   = true
    cookies_config {
      cookie_behavior = "none"
    }
    headers_config {
      header_behavior = "none"
    }
    query_strings_config {
      query_string_behavior = "all"
    }
  }
}

resource "aws_cloudfront_response_headers_policy" "security" {
  name = "${local.name}-security-headers"
  security_headers_config {
    strict_transport_security {
      access_control_max_age_sec = 31536000
      include_subdomains         = true
      preload                    = false
      override                   = true
    }
    content_type_options {
      override = true
    }
    frame_options {
      frame_option = "DENY"
      override     = true
    }
    referrer_policy {
      referrer_policy = "strict-origin-when-cross-origin"
      override        = true
    }
    content_security_policy {
      content_security_policy = "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
      override                = false
    }
  }
}

resource "aws_cloudfront_distribution" "this" {
  enabled             = true
  comment             = "PolyCodeBench ${var.environment}"
  aliases             = var.domain_names
  price_class         = var.price_class
  http_version        = "http2and3"
  is_ipv6_enabled     = true
  web_acl_id          = var.web_acl_arn
  default_root_object = null

  origin {
    origin_id                = "public-projections"
    domain_name              = var.public_bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.public.id
  }

  origin {
    origin_id   = "control-alb"
    domain_name = var.alb_dns_name
    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    target_origin_id           = "control-alb"
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods             = ["GET", "HEAD"]
    cache_policy_id            = data.aws_cloudfront_cache_policy.disabled.id
    origin_request_policy_id   = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.security.id
    compress                   = true
  }

  ordered_cache_behavior {
    path_pattern               = "/v1/public/*"
    target_origin_id           = "control-alb"
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD", "OPTIONS"]
    cached_methods             = ["GET", "HEAD"]
    cache_policy_id            = aws_cloudfront_cache_policy.api.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.security.id
    compress                   = true
  }

  dynamic "ordered_cache_behavior" {
    for_each = toset(["/releases/*", "/keys/*"])
    content {
      path_pattern               = ordered_cache_behavior.value
      target_origin_id           = "public-projections"
      viewer_protocol_policy     = "redirect-to-https"
      allowed_methods            = ["GET", "HEAD"]
      cached_methods             = ["GET", "HEAD"]
      cache_policy_id            = data.aws_cloudfront_cache_policy.optimized.id
      response_headers_policy_id = aws_cloudfront_response_headers_policy.security.id
      compress                   = true
    }
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    acm_certificate_arn      = var.cdn_certificate_arn
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
  }

  logging_config {
    bucket          = var.logs_bucket_domain_name
    prefix          = "cloudfront/"
    include_cookies = false
  }

  tags = local.tags
}

data "aws_iam_policy_document" "public_bucket" {
  source_policy_documents = [var.public_base_policy_json]

  statement {
    sid       = "CloudFrontOriginRead"
    actions   = ["s3:GetObject"]
    resources = ["arn:aws:s3:::${var.public_bucket_id}/*"]
    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.this.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "public" {
  bucket = var.public_bucket_id
  policy = data.aws_iam_policy_document.public_bucket.json
}

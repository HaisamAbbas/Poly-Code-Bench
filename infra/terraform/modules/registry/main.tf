# OCI registry for control-service images and approved guest images (T 22.3).
# Tags are immutable and services reference images by digest only; promotion of a worker
# image still requires the conformance checks and the digest recorded in config/images/.

locals {
  name = "pcb-${var.environment}"
  tags = merge(var.tags, { "pcb:environment" = var.environment })
}

resource "aws_ecr_repository" "this" {
  for_each             = toset(var.repositories)
  name                 = "${local.name}/${each.key}"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = var.data_key_arn
  }

  tags = merge(local.tags, { "pcb:image" = each.key })
}

resource "aws_ecr_lifecycle_policy" "this" {
  for_each   = aws_ecr_repository.this
  repository = each.value.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Expire untagged layers left by interrupted pushes"
      selection = {
        tagStatus   = "untagged"
        countType   = "sinceImagePushed"
        countUnit   = "days"
        countNumber = 14
      }
      action = { type = "expire" }
    }]
  })
}

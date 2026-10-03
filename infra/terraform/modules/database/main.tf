# Managed PostgreSQL 17 with point-in-time recovery (A 15.1, T 22.6).
#
# RPO/RTO in T 22.6 (<=15 min / <=4 h) are TARGETS. RDS continuous backup gives PITR at
# roughly five-minute granularity; the restore rehearsal (pcb-ops restore rehearse) measures
# the achieved recovery time and records it, and only that measurement is reported.

locals {
  name = "pcb-${var.environment}"
  tags = merge(var.tags, { "pcb:environment" = var.environment })
}

resource "aws_db_subnet_group" "this" {
  name       = "${local.name}-data"
  subnet_ids = var.data_subnet_ids
  tags       = local.tags
}

resource "aws_security_group" "database" {
  name_prefix = "${local.name}-postgres-"
  description = "PostgreSQL reachable from control services only"
  vpc_id      = var.vpc_id
  tags        = merge(local.tags, { Name = "${local.name}-postgres" })
}

resource "aws_vpc_security_group_ingress_rule" "from_control" {
  security_group_id            = aws_security_group.database.id
  description                  = "PostgreSQL from control services"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = var.control_security_group_id
}

resource "aws_db_parameter_group" "this" {
  name_prefix = "${local.name}-pg17-"
  family      = "postgres17"
  description = "PolyCodeBench ${var.environment} PostgreSQL 17"

  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
  parameter {
    name  = "log_min_duration_statement"
    value = "1000"
  }
  # Statement text can contain hidden task bodies passed as literals by a buggy caller;
  # never log full statements. Errors are logged without bind parameters.
  parameter {
    name  = "log_statement"
    value = "none"
  }
  parameter {
    name  = "log_parameter_max_length_on_error"
    value = "0"
  }
  parameter {
    name         = "idle_in_transaction_session_timeout"
    value        = "300000"
    apply_method = "pending-reboot"
  }

  lifecycle {
    create_before_destroy = true
  }
  tags = local.tags
}

resource "aws_db_instance" "this" {
  identifier                            = "${local.name}-postgres"
  engine                                = "postgres"
  engine_version                        = var.engine_version
  instance_class                        = var.instance_class
  allocated_storage                     = var.allocated_storage_gb
  max_allocated_storage                 = var.max_allocated_storage_gb
  storage_type                          = "gp3"
  storage_encrypted                     = true
  kms_key_id                            = var.data_key_arn
  db_name                               = "polycodebench"
  username                              = "pcb_admin"
  manage_master_user_password           = true
  master_user_secret_kms_key_id         = var.secrets_key_arn
  iam_database_authentication_enabled   = true
  db_subnet_group_name                  = aws_db_subnet_group.this.name
  vpc_security_group_ids                = [aws_security_group.database.id]
  parameter_group_name                  = aws_db_parameter_group.this.name
  publicly_accessible                   = false
  multi_az                              = var.multi_az
  backup_retention_period               = var.backup_retention_days
  backup_window                         = "03:00-03:30"
  maintenance_window                    = "sun:04:00-sun:05:00"
  copy_tags_to_snapshot                 = true
  delete_automated_backups              = false
  deletion_protection                   = true
  skip_final_snapshot                   = false
  final_snapshot_identifier             = "${local.name}-postgres-final"
  auto_minor_version_upgrade            = false
  allow_major_version_upgrade           = false
  apply_immediately                     = false
  performance_insights_enabled          = true
  performance_insights_kms_key_id       = var.data_key_arn
  performance_insights_retention_period = 7
  monitoring_interval                   = 60
  monitoring_role_arn                   = var.monitoring_role_arn
  enabled_cloudwatch_logs_exports       = ["postgresql"]
  tags                                  = merge(local.tags, { "pcb:backup" = "required" })
}

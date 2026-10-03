# Trusted control services, separated from execution hosts (A 15.1, T 2, T 22.2).
#
# Each process role runs as its own ECS service with its own task role (modules/identity), so
# the AWS principal a process presents is what identifies its environment and role. The
# process verifies that principal at startup (`pcb-ops identity verify`, run as the container
# entrypoint guard) against config/environments/<env>.yaml before serving.
# Images are referenced by digest only. Root filesystems are read-only; containers are
# non-root; no service is privileged.

data "aws_ec2_managed_prefix_list" "cloudfront" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

locals {
  name = "pcb-${var.environment}"
  tags = merge(var.tags, { "pcb:environment" = var.environment })

  long_running = { for name, svc in var.services : name => svc if svc.desired_count > 0 }
  with_port    = { for name, svc in var.services : name => svc if svc.port != null }

  base_environment = {
    PCB_ENVIRONMENT    = var.environment
    PCB_OTEL_ENDPOINT  = var.otel_endpoint
    PCB_METRICS_PORT   = tostring(var.metrics_port)
    PCB_LOG_FORMAT     = "json"
    PCB_ENV_MANIFEST   = "/etc/pcb/environment.yaml"
    AWS_REGION         = var.region
    AWS_DEFAULT_REGION = var.region
  }
}

resource "aws_ecs_cluster" "this" {
  name = local.name
  setting {
    name  = "containerInsights"
    value = "enhanced"
  }
  tags = local.tags
}

# ---------------------------------------------------------------- network
resource "aws_security_group" "alb" {
  name_prefix = "${local.name}-alb-"
  description = "Public entry, reachable only from CloudFront origin-facing addresses"
  vpc_id      = var.vpc_id
  tags        = merge(local.tags, { Name = "${local.name}-alb" })
}

resource "aws_vpc_security_group_ingress_rule" "alb_from_cloudfront" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTPS from CloudFront only"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  prefix_list_id    = data.aws_ec2_managed_prefix_list.cloudfront.id
}

resource "aws_vpc_security_group_egress_rule" "alb_to_services" {
  for_each                     = local.with_port
  security_group_id            = aws_security_group.alb.id
  description                  = "To ${each.key}"
  ip_protocol                  = "tcp"
  from_port                    = each.value.port
  to_port                      = each.value.port
  referenced_security_group_id = var.control_security_group_id
}

resource "aws_vpc_security_group_ingress_rule" "services_from_alb" {
  for_each                     = local.with_port
  security_group_id            = var.control_security_group_id
  description                  = "${each.key} from ALB"
  ip_protocol                  = "tcp"
  from_port                    = each.value.port
  to_port                      = each.value.port
  referenced_security_group_id = aws_security_group.alb.id
}

resource "aws_lb" "public" {
  name                       = "${local.name}-public"
  load_balancer_type         = "application"
  internal                   = false
  subnets                    = var.public_subnet_ids
  security_groups            = [aws_security_group.alb.id]
  drop_invalid_header_fields = true
  enable_deletion_protection = var.environment == "production"
  access_logs {
    bucket  = var.access_log_bucket
    prefix  = "alb"
    enabled = true
  }
  tags = local.tags
}

resource "aws_lb_target_group" "this" {
  for_each    = local.with_port
  name        = substr("${local.name}-${each.key}", 0, 32)
  port        = each.value.port
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = var.vpc_id
  health_check {
    path                = each.value.health_path
    matcher             = "200"
    interval            = 15
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
  deregistration_delay = 30
  tags                 = local.tags
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.public.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.alb_certificate_arn
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.this["web"].arn
  }
}

resource "aws_lb_listener_rule" "api" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 10
  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.this["api"].arn
  }
  condition {
    path_pattern {
      values = ["/v1/*", "/healthz"]
    }
  }
}

# ---------------------------------------------------------------- tasks
resource "aws_ecs_task_definition" "this" {
  for_each                 = var.services
  family                   = "${local.name}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = each.value.cpu
  memory                   = each.value.memory
  execution_role_arn       = var.task_execution_role_arn
  task_role_arn            = var.task_role_arns[each.value.role]
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode(concat([
    {
      name                   = each.key
      image                  = each.value.image
      essential              = true
      user                   = "10001:10001"
      readonlyRootFilesystem = true
      privileged             = false
      # Every Python service starts behind the identity guard: it verifies the task's STS
      # principal against config/environments/<env>.yaml and only then execs the command.
      # The web (Node) image holds no secrets and reads only the public API.
      entryPoint = each.value.role == "web" ? null : [
        "pcb-ops", "identity", "verify", "--role", each.value.role, "--exec", "--",
      ]
      command         = each.value.command
      linuxParameters = { initProcessEnabled = true, capabilities = { drop = ["ALL"] } }
      portMappings    = each.value.port == null ? [] : [{ containerPort = each.value.port, protocol = "tcp" }]
      environment = [
        for key, value in merge(local.base_environment, { PCB_ROLE = each.value.role }, each.value.environment) :
        { name = key, value = value }
      ]
      secrets     = [for key, arn in each.value.secrets : { name = key, valueFrom = arn }]
      mountPoints = [{ sourceVolume = "tmp", containerPath = "/tmp", readOnly = false }]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = "/pcb/${var.environment}/${each.value.role}"
          awslogs-region        = var.region
          awslogs-stream-prefix = each.key
        }
      }
    }],
    var.otel_collector_image == null ? [] : [{
      name                   = "otel-collector"
      image                  = var.otel_collector_image
      essential              = false
      user                   = "10001:10001"
      readonlyRootFilesystem = true
      command                = ["--config=env:PCB_OTEL_COLLECTOR_CONFIG"]
      environment = [{
        name  = "PCB_OTEL_COLLECTOR_CONFIG"
        value = templatefile("${path.module}/otel-collector.yaml.tftpl", { metrics_port = var.metrics_port, remote_write_url = var.prometheus_remote_write_url, region = var.region, environment = var.environment, role = each.value.role })
      }]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = "/pcb/${var.environment}/otel-collector"
          awslogs-region        = var.region
          awslogs-stream-prefix = each.key
        }
      }
    }]
  ))

  volume {
    name = "tmp"
  }

  tags = merge(local.tags, { "pcb:role" = each.value.role })
}

resource "aws_ecs_service" "this" {
  for_each                           = local.long_running
  name                               = each.key
  cluster                            = aws_ecs_cluster.this.id
  task_definition                    = aws_ecs_task_definition.this[each.key].arn
  desired_count                      = each.value.desired_count
  launch_type                        = "FARGATE"
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  enable_execute_command             = false
  propagate_tags                     = "TASK_DEFINITION"
  wait_for_steady_state              = true

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = var.control_subnet_ids
    security_groups  = [var.control_security_group_id]
    assign_public_ip = false
  }

  dynamic "load_balancer" {
    for_each = each.value.port == null ? [] : [each.value.port]
    content {
      target_group_arn = aws_lb_target_group.this[each.key].arn
      container_name   = each.key
      container_port   = load_balancer.value
    }
  }

  tags = merge(local.tags, { "pcb:role" = each.value.role })
}

# ---------------------------------------------------------------- scheduled operations
data "aws_iam_policy_document" "scheduler_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${local.name}-ops-schedules"
  assume_role_policy = data.aws_iam_policy_document.scheduler_trust.json
  tags               = merge(local.tags, { "pcb:role" = "ops-schedules" })
}

resource "aws_iam_role_policy" "scheduler" {
  name = "run-ops-tasks"
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ecs:RunTask"]
        Resource = [for name, schedule in var.schedules : aws_ecs_task_definition.this[schedule.service].arn_without_revision]
      },
      {
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = concat([var.task_execution_role_arn], [for name, schedule in var.schedules : var.task_role_arns[var.services[schedule.service].role]])
      },
    ]
  })
}

resource "aws_scheduler_schedule" "this" {
  for_each                     = var.schedules
  name                         = "${local.name}-${each.key}"
  schedule_expression          = each.value.expression
  schedule_expression_timezone = "UTC"
  flexible_time_window {
    mode = "OFF"
  }
  target {
    arn      = aws_ecs_cluster.this.arn
    role_arn = aws_iam_role.scheduler.arn
    ecs_parameters {
      task_definition_arn = aws_ecs_task_definition.this[each.value.service].arn_without_revision
      launch_type         = "FARGATE"
      network_configuration {
        subnets          = var.control_subnet_ids
        security_groups  = [var.control_security_group_id]
        assign_public_ip = false
      }
    }
    input = jsonencode({
      containerOverrides = [{ name = each.value.service, command = each.value.command }]
    })
    retry_policy {
      maximum_retry_attempts = 0
    }
  }
}

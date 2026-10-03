output "cluster_arn" {
  value       = aws_ecs_cluster.this.arn
  description = "ECS cluster."
}

output "alb_dns_name" {
  value       = aws_lb.public.dns_name
  description = "Origin load balancer for CloudFront."
}

output "task_definition_arns" {
  value       = { for name, def in aws_ecs_task_definition.this : name => def.arn }
  description = "Task definitions (runbooks start migrator/restore tasks from these)."
}

output "vpc_id" {
  value       = aws_vpc.this.id
  description = "Environment VPC."
}

output "vpc_cidr" {
  value       = aws_vpc.this.cidr_block
  description = "Environment VPC block."
}

output "subnet_ids_by_tier" {
  value = {
    for tier in ["public", "control", "data", "solve", "grading", "admission", "performance"] :
    tier => [for key, subnet in aws_subnet.this : subnet.id if subnet.tags["pcb:tier"] == tier]
  }
  description = "Subnet IDs grouped by isolation tier."
}

output "control_cidrs" {
  value       = [for key, subnet in aws_subnet.this : subnet.cidr_block if subnet.tags["pcb:tier"] == "control"]
  description = "Control-tier CIDRs (supervisors run here)."
}

output "endpoint_security_group_id" {
  value       = aws_security_group.endpoints.id
  description = "Security group of the interface endpoints."
}

output "s3_gateway_endpoint_id" {
  value       = aws_vpc_endpoint.s3.id
  description = "S3 gateway endpoint; bucket policies pin internal access to it."
}

output "control_security_group_id" {
  value       = aws_security_group.control.id
  description = "Control-service security group (database ingress, guest SSH source)."
}

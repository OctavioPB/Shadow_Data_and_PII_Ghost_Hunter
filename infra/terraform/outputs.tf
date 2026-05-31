output "vpc_id" {
  description = "VPC ID"
  value       = aws_vpc.main.id
}

output "private_subnet_ids" {
  description = "Private subnet IDs (used by EKS node groups, RDS, MSK)"
  value       = aws_subnet.private[*].id
}

output "public_subnet_ids" {
  description = "Public subnet IDs (used by load balancers)"
  value       = aws_subnet.public[*].id
}

output "eks_cluster_name" {
  description = "EKS cluster name — use with: aws eks update-kubeconfig --name <value>"
  value       = aws_eks_cluster.main.name
}

output "eks_cluster_endpoint" {
  description = "EKS API server endpoint"
  value       = aws_eks_cluster.main.endpoint
}

output "eks_cluster_certificate_authority" {
  description = "Base64-encoded CA certificate for the EKS cluster"
  value       = aws_eks_cluster.main.certificate_authority[0].data
  sensitive   = true
}

output "rds_endpoint" {
  description = "RDS PostgreSQL endpoint (host:port)"
  value       = "${aws_db_instance.main.address}:${aws_db_instance.main.port}"
}

output "rds_database_name" {
  value = aws_db_instance.main.db_name
}

output "redis_primary_endpoint" {
  description = "ElastiCache Redis primary endpoint"
  value       = aws_elasticache_replication_group.main.primary_endpoint_address
}

output "msk_bootstrap_brokers_tls" {
  description = "MSK TLS bootstrap broker string (comma-separated)"
  value       = aws_msk_cluster.main.bootstrap_brokers_sasl_scram
  sensitive   = true
}

output "quarantine_bucket_name" {
  description = "S3 quarantine bucket — write-only for pipeline, read restricted to DPO IAM role"
  value       = aws_s3_bucket.quarantine.bucket
}

output "data_lake_bucket_name" {
  value = aws_s3_bucket.data_lake.bucket
}

output "models_bucket_name" {
  value = aws_s3_bucket.models.bucket
}

output "staging_bucket_name" {
  value = aws_s3_bucket.staging.bucket
}

output "acm_certificate_arn" {
  description = "ACM certificate ARN — set in Helm values as ingress.tls.certificateArn"
  value       = aws_acm_certificate_validation.api.certificate_arn
}

output "api_fqdn" {
  value = "api.${var.root_domain}"
}

output "dashboard_fqdn" {
  value = "dashboard.${var.root_domain}"
}

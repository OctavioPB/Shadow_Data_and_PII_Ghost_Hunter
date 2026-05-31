variable "aws_region" {
  description = "AWS region for all resources"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment"
  type        = string
  validation {
    condition     = contains(["dev", "staging", "production"], var.environment)
    error_message = "environment must be dev, staging, or production"
  }
}

# ─── S3 ───────────────────────────────────────────────────────────────────────

variable "s3_data_lake_bucket" {
  description = "S3 bucket name for the data lake"
  type        = string
  default     = "pii-hunter-datalake"
}

variable "s3_quarantine_bucket" {
  description = "S3 bucket name for quarantined PII data"
  type        = string
  default     = "pii-quarantine"
}

variable "s3_models_bucket" {
  description = "S3 bucket name for ML model artifacts"
  type        = string
  default     = "pii-hunter-models"
}

variable "s3_staging_bucket" {
  description = "S3 bucket for column samples staging"
  type        = string
  default     = "pii-hunter-staging"
}

# ─── VPC ──────────────────────────────────────────────────────────────────────

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "availability_zones" {
  description = "List of AZs to deploy into (determines subnet count)"
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b", "us-east-1c"]
}

# ─── EKS ──────────────────────────────────────────────────────────────────────

variable "eks_kubernetes_version" {
  description = "Kubernetes version for the EKS cluster"
  type        = string
  default     = "1.29"
}

variable "eks_public_access_cidrs" {
  description = "CIDRs allowed to reach the EKS public API endpoint"
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "eks_api_instance_type" {
  description = "EC2 instance type for the API node group"
  type        = string
  default     = "m6i.xlarge"
}

variable "eks_api_desired_size" {
  type    = number
  default = 2
}

variable "eks_api_min_size" {
  type    = number
  default = 1
}

variable "eks_api_max_size" {
  type    = number
  default = 6
}

variable "eks_inference_instance_type" {
  description = "EC2 instance type for the inference node group (GPU optional)"
  type        = string
  default     = "g4dn.xlarge"
}

variable "eks_inference_desired_size" {
  type    = number
  default = 2
}

variable "eks_inference_max_size" {
  type    = number
  default = 8
}

# ─── RDS ──────────────────────────────────────────────────────────────────────

variable "rds_instance_class" {
  description = "RDS instance class"
  type        = string
  default     = "db.m6g.large"
}

variable "rds_allocated_storage" {
  description = "Initial storage in GB"
  type        = number
  default     = 100
}

variable "rds_max_allocated_storage" {
  description = "Maximum autoscaled storage in GB"
  type        = number
  default     = 500
}

variable "rds_username" {
  description = "Master username for the RDS instance"
  type        = string
  default     = "piighostadmin"
  sensitive   = true
}

variable "rds_password" {
  description = "Master password for the RDS instance — supply via TF_VAR_rds_password"
  type        = string
  sensitive   = true
}

# ─── ElastiCache ──────────────────────────────────────────────────────────────

variable "redis_node_type" {
  description = "ElastiCache node type"
  type        = string
  default     = "cache.m6g.large"
}

# ─── MSK ──────────────────────────────────────────────────────────────────────

variable "msk_broker_instance_type" {
  description = "MSK broker EC2 instance type"
  type        = string
  default     = "kafka.m5.large"
}

variable "msk_broker_volume_size_gb" {
  description = "EBS volume size per broker in GB"
  type        = number
  default     = 500
}

# ─── DNS / TLS ────────────────────────────────────────────────────────────────

variable "root_domain" {
  description = "Route53 hosted zone root domain (e.g. piidetect.example.com)"
  type        = string
  default     = "piidetect.example.com"
}

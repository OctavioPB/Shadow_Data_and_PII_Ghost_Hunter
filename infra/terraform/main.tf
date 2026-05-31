# ─── S3 Buckets ───────────────────────────────────────────────────────────────

resource "aws_s3_bucket" "data_lake" {
  bucket = "${var.s3_data_lake_bucket}-${var.environment}"
}

resource "aws_s3_bucket_versioning" "data_lake" {
  bucket = aws_s3_bucket.data_lake.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data_lake" {
  bucket = aws_s3_bucket.data_lake.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket" "quarantine" {
  bucket = "${var.s3_quarantine_bucket}-${var.environment}"
}

resource "aws_s3_bucket_versioning" "quarantine" {
  bucket = aws_s3_bucket.quarantine.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "quarantine" {
  bucket = aws_s3_bucket.quarantine.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "quarantine" {
  bucket                  = aws_s3_bucket.quarantine.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# [S8-04] Quarantine data retention: DPO review window = 30 days, then auto-expire.
# Objects in /pending/ are moved to /expired/ after 23 days (7-day warning period)
# and permanently deleted after 30 days from creation.
resource "aws_s3_bucket_lifecycle_configuration" "quarantine" {
  bucket = aws_s3_bucket.quarantine.id

  rule {
    id     = "quarantine-expiry-30d"
    status = "Enabled"

    filter {
      prefix = "pending/"
    }

    # Transition to GLACIER after 23 days — signals the DAG to send DPO warning
    transition {
      days          = 23
      storage_class = "GLACIER"
    }

    # Hard-delete after 30 days — GDPR Article 17 erasure deadline
    expiration {
      days = 30
    }
  }

  rule {
    id     = "quarantine-expired-cleanup"
    status = "Enabled"

    filter {
      prefix = "expired/"
    }

    expiration {
      days = 1
    }
  }
}

resource "aws_s3_bucket" "models" {
  bucket = "${var.s3_models_bucket}-${var.environment}"
}

resource "aws_s3_bucket_versioning" "models" {
  bucket = aws_s3_bucket.models.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "models" {
  bucket = aws_s3_bucket.models.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket" "staging" {
  bucket = "${var.s3_staging_bucket}-${var.environment}"
}

resource "aws_s3_bucket_server_side_encryption_configuration" "staging" {
  bucket = aws_s3_bucket.staging.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Staging samples are ephemeral — auto-delete after 7 days
resource "aws_s3_bucket_lifecycle_configuration" "staging" {
  bucket = aws_s3_bucket.staging.id

  rule {
    id     = "staging-samples-7d"
    status = "Enabled"
    filter { prefix = "samples/" }
    expiration { days = 7 }
  }
}

# ─── VPC ──────────────────────────────────────────────────────────────────────

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = { Name = "pii-ghost-hunter-${var.environment}" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "pii-ghost-hunter-igw-${var.environment}" }
}

resource "aws_subnet" "public" {
  count                   = length(var.availability_zones)
  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 4, count.index)
  availability_zone       = var.availability_zones[count.index]
  map_public_ip_on_launch = true

  tags = {
    Name                                          = "pii-gh-public-${var.availability_zones[count.index]}"
    "kubernetes.io/role/elb"                      = "1"
    "kubernetes.io/cluster/pii-ghost-hunter-${var.environment}" = "shared"
  }
}

resource "aws_subnet" "private" {
  count             = length(var.availability_zones)
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, count.index + length(var.availability_zones))
  availability_zone = var.availability_zones[count.index]

  tags = {
    Name                                          = "pii-gh-private-${var.availability_zones[count.index]}"
    "kubernetes.io/role/internal-elb"             = "1"
    "kubernetes.io/cluster/pii-ghost-hunter-${var.environment}" = "shared"
  }
}

resource "aws_eip" "nat" {
  count  = length(var.availability_zones)
  domain = "vpc"
  tags   = { Name = "pii-gh-nat-eip-${count.index}" }
}

resource "aws_nat_gateway" "main" {
  count         = length(var.availability_zones)
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id
  tags          = { Name = "pii-gh-nat-${var.availability_zones[count.index]}" }
  depends_on    = [aws_internet_gateway.main]
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
  tags = { Name = "pii-gh-public-rt" }
}

resource "aws_route_table_association" "public" {
  count          = length(var.availability_zones)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  count  = length(var.availability_zones)
  vpc_id = aws_vpc.main.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main[count.index].id
  }
  tags = { Name = "pii-gh-private-rt-${count.index}" }
}

resource "aws_route_table_association" "private" {
  count          = length(var.availability_zones)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

# ─── EKS ──────────────────────────────────────────────────────────────────────

resource "aws_iam_role" "eks_cluster" {
  name = "pii-gh-eks-cluster-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "eks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "eks_cluster_policy" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
  role       = aws_iam_role.eks_cluster.name
}

resource "aws_eks_cluster" "main" {
  name     = "pii-ghost-hunter-${var.environment}"
  role_arn = aws_iam_role.eks_cluster.arn
  version  = var.eks_kubernetes_version

  vpc_config {
    subnet_ids              = concat(aws_subnet.private[*].id, aws_subnet.public[*].id)
    endpoint_private_access = true
    endpoint_public_access  = true
    public_access_cidrs     = var.eks_public_access_cidrs
  }

  enabled_cluster_log_types = ["api", "audit", "authenticator", "controllerManager", "scheduler"]

  depends_on = [aws_iam_role_policy_attachment.eks_cluster_policy]
}

resource "aws_iam_role" "eks_node_group" {
  name = "pii-gh-eks-nodes-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "eks_worker_node_policy" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy"
  role       = aws_iam_role.eks_node_group.name
}

resource "aws_iam_role_policy_attachment" "eks_cni_policy" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy"
  role       = aws_iam_role.eks_node_group.name
}

resource "aws_iam_role_policy_attachment" "eks_ecr_read_only" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
  role       = aws_iam_role.eks_node_group.name
}

resource "aws_eks_node_group" "api" {
  cluster_name    = aws_eks_cluster.main.name
  node_group_name = "api-${var.environment}"
  node_role_arn   = aws_iam_role.eks_node_group.arn
  subnet_ids      = aws_subnet.private[*].id
  instance_types  = [var.eks_api_instance_type]

  scaling_config {
    desired_size = var.eks_api_desired_size
    min_size     = var.eks_api_min_size
    max_size     = var.eks_api_max_size
  }

  labels = { workload = "api" }
  depends_on = [
    aws_iam_role_policy_attachment.eks_worker_node_policy,
    aws_iam_role_policy_attachment.eks_cni_policy,
    aws_iam_role_policy_attachment.eks_ecr_read_only,
  ]
}

resource "aws_eks_node_group" "inference" {
  cluster_name    = aws_eks_cluster.main.name
  node_group_name = "inference-${var.environment}"
  node_role_arn   = aws_iam_role.eks_node_group.arn
  subnet_ids      = aws_subnet.private[*].id
  instance_types  = [var.eks_inference_instance_type]

  scaling_config {
    desired_size = var.eks_inference_desired_size
    min_size     = 1
    max_size     = var.eks_inference_max_size
  }

  labels = { workload = "inference" }
  depends_on = [
    aws_iam_role_policy_attachment.eks_worker_node_policy,
    aws_iam_role_policy_attachment.eks_cni_policy,
    aws_iam_role_policy_attachment.eks_ecr_read_only,
  ]
}

# ─── RDS — PostgreSQL 15 ─────────────────────────────────────────────────────

resource "aws_db_subnet_group" "main" {
  name       = "pii-gh-db-${var.environment}"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_security_group" "rds" {
  name   = "pii-gh-rds-${var.environment}"
  vpc_id = aws_vpc.main.id

  ingress {
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_db_parameter_group" "postgres15" {
  name   = "pii-gh-postgres15-${var.environment}"
  family = "postgres15"

  parameter {
    name  = "log_connections"
    value = "1"
  }
  parameter {
    name  = "log_disconnections"
    value = "1"
  }
  # Enforce SSL for all connections
  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
}

resource "aws_db_instance" "main" {
  identifier              = "pii-gh-${var.environment}"
  engine                  = "postgres"
  engine_version          = "15.6"
  instance_class          = var.rds_instance_class
  allocated_storage       = var.rds_allocated_storage
  max_allocated_storage   = var.rds_max_allocated_storage
  storage_type            = "gp3"
  storage_encrypted       = true
  db_name                 = "piighostdb"
  username                = var.rds_username
  password                = var.rds_password
  parameter_group_name    = aws_db_parameter_group.postgres15.name
  db_subnet_group_name    = aws_db_subnet_group.main.name
  vpc_security_group_ids  = [aws_security_group.rds.id]
  multi_az                = var.environment == "production"
  backup_retention_period = 7
  deletion_protection     = var.environment == "production"
  skip_final_snapshot     = var.environment != "production"
  final_snapshot_identifier = var.environment == "production" ? "pii-gh-final-${var.environment}" : null

  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]

  tags = { Name = "pii-gh-postgres-${var.environment}" }
}

# ─── ElastiCache — Redis 7 ────────────────────────────────────────────────────

resource "aws_elasticache_subnet_group" "main" {
  name       = "pii-gh-cache-${var.environment}"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_security_group" "redis" {
  name   = "pii-gh-redis-${var.environment}"
  vpc_id = aws_vpc.main.id

  ingress {
    from_port   = 6379
    to_port     = 6379
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_elasticache_replication_group" "main" {
  replication_group_id = "pii-gh-${var.environment}"
  description          = "PII Ghost-Hunter Redis cache"
  node_type            = var.redis_node_type
  num_cache_clusters   = var.environment == "production" ? 2 : 1
  port                 = 6379
  subnet_group_name    = aws_elasticache_subnet_group.main.name
  security_group_ids   = [aws_security_group.redis.id]
  engine_version       = "7.0"
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  automatic_failover_enabled = var.environment == "production"

  tags = { Name = "pii-gh-redis-${var.environment}" }
}

# ─── MSK — Kafka ──────────────────────────────────────────────────────────────

resource "aws_security_group" "msk" {
  name   = "pii-gh-msk-${var.environment}"
  vpc_id = aws_vpc.main.id

  ingress {
    from_port   = 9096
    to_port     = 9096
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }
  ingress {
    from_port   = 2181
    to_port     = 2181
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_msk_configuration" "main" {
  name              = "pii-gh-msk-${var.environment}"
  kafka_versions    = ["3.6.0"]
  server_properties = <<-EOT
    auto.create.topics.enable=false
    default.replication.factor=3
    min.insync.replicas=2
    num.partitions=6
    log.retention.hours=168
    offsets.topic.replication.factor=3
  EOT
}

resource "aws_msk_cluster" "main" {
  cluster_name           = "pii-gh-${var.environment}"
  kafka_version          = "3.6.0"
  number_of_broker_nodes = length(var.availability_zones)

  broker_node_group_info {
    instance_type   = var.msk_broker_instance_type
    client_subnets  = aws_subnet.private[*].id
    security_groups = [aws_security_group.msk.id]

    storage_info {
      ebs_storage_info {
        volume_size = var.msk_broker_volume_size_gb
      }
    }
  }

  encryption_info {
    encryption_in_transit {
      client_broker = "TLS"
      in_cluster    = true
    }
  }

  client_authentication {
    sasl {
      scram = true
    }
  }

  configuration_info {
    arn      = aws_msk_configuration.main.arn
    revision = aws_msk_configuration.main.latest_revision
  }

  logging_info {
    broker_logs {
      cloudwatch_logs {
        enabled   = true
        log_group = "/aws/msk/pii-gh-${var.environment}"
      }
    }
  }
}

# ─── DNS & TLS — Route53 + ACM ────────────────────────────────────────────────

data "aws_route53_zone" "main" {
  name         = var.root_domain
  private_zone = false
}

resource "aws_acm_certificate" "api" {
  domain_name               = "api.${var.root_domain}"
  subject_alternative_names = ["dashboard.${var.root_domain}"]
  validation_method         = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "acm_validation" {
  for_each = {
    for dvo in aws_acm_certificate.api.domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  }

  allow_overwrite = true
  name            = each.value.name
  records         = [each.value.record]
  ttl             = 60
  type            = each.value.type
  zone_id         = data.aws_route53_zone.main.zone_id
}

resource "aws_acm_certificate_validation" "api" {
  certificate_arn         = aws_acm_certificate.api.arn
  validation_record_fqdns = [for record in aws_route53_record.acm_validation : record.fqdn]
}

# ─── Terraform state bootstrap (run once manually before backend init) ────────
# These resources are not managed by this state file — create them via AWS CLI:
#
#   aws s3api create-bucket --bucket pii-ghost-hunter-tfstate --region us-east-1
#   aws s3api put-bucket-versioning \
#       --bucket pii-ghost-hunter-tfstate \
#       --versioning-configuration Status=Enabled
#   aws s3api put-bucket-encryption \
#       --bucket pii-ghost-hunter-tfstate \
#       --server-side-encryption-configuration \
#       '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
#   aws dynamodb create-table \
#       --table-name pii-ghost-hunter-tfstate-lock \
#       --attribute-definitions AttributeName=LockID,AttributeType=S \
#       --key-schema AttributeName=LockID,KeyType=HASH \
#       --billing-mode PAY_PER_REQUEST \
#       --region us-east-1

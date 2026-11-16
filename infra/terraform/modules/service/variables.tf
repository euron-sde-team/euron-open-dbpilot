variable "name_prefix" {
  description = "Deployment prefix, e.g. \"dbpilot-prod\". At most 24 characters, so target group names stay unique within AWS's 32-character limit."
  type        = string
  validation {
    condition     = length(var.name_prefix) <= 24
    error_message = "name_prefix must be at most 24 characters."
  }
}

variable "vpc_id" {
  description = "VPC of the load balancer and the tasks."
  type        = string
}

variable "public_subnet_ids" {
  description = "Public subnets for the load balancer."
  type        = list(string)
}

variable "private_subnet_ids" {
  description = "Private subnets the tasks run in."
  type        = list(string)
}

variable "hostname" {
  description = "Public host name of the web app and API, e.g. dbpilot.example.com. The certificate must cover it."
  type        = string
}

variable "certificate_arn" {
  description = "ACM certificate (same region) for the HTTPS listener, covering hostname."
  type        = string
}

variable "route53_zone_id" {
  description = "Hosted zone to create the hostname alias record in. Null: point DNS at alb_dns_name yourself."
  type        = string
  default     = null
}

variable "api_image" {
  description = "Full api image reference (also runs the worker and migrate), e.g. <registry>/dbpilot-prod/api:<tag>."
  type        = string
}

variable "web_image" {
  description = "Full web image reference, e.g. <registry>/dbpilot-prod/web:<tag>."
  type        = string
}

variable "api_port" {
  description = "Port the api container listens on (passed to `dbpilot serve --port`)."
  type        = number
  default     = 8080
}

variable "web_port" {
  description = "Port the web container listens on (PORT)."
  type        = number
  default     = 3000
}

variable "environment" {
  description = "Plain environment for api, worker and migrate (PUBLIC_URL and OBJECTS_BUCKET are set by the module). Never secrets."
  type        = map(string)
  default     = {}
}

variable "web_environment" {
  description = "Plain environment for web (PORT, HOSTNAME and API_INTERNAL_URL are set by the module). Never secrets."
  type        = map(string)
  default     = {}
}

variable "secrets" {
  description = "Secrets injected into api, worker and migrate: [{ name, valueFrom = \"<secret arn>:<json key>::\" }] (secrets module ecs_secrets)."
  type        = list(object({ name = string, valueFrom = string }))
  default     = []
}

variable "kms_key_arn" {
  description = "CMK that seals connection secrets and the AI provider key (encryption context dbpilot:record), encrypts the objects bucket and the injected secrets."
  type        = string
}

variable "objects_bucket_arn" {
  description = "Objects bucket (storage module bucket_arn)."
  type        = string
}

variable "objects_bucket_name" {
  description = "Objects bucket name (storage module bucket_name), passed as OBJECTS_BUCKET."
  type        = string
}

variable "db_security_group_id" {
  description = "Database security group; api, worker and migrate get ingress on it."
  type        = string
}

variable "db_port" {
  description = "Database port."
  type        = number
  default     = 5432
}

variable "cache_security_group_id" {
  description = "Cache security group; api, worker and migrate get ingress on it."
  type        = string
}

variable "cache_port" {
  description = "Cache port."
  type        = number
  default     = 6379
}

variable "log_group_names" {
  description = "CloudWatch log group per app: { api, worker, web, migrate } (observability module log_group_names)."
  type        = map(string)
  validation {
    condition     = alltrue([for k in ["api", "worker", "web", "migrate"] : contains(keys(var.log_group_names), k)])
    error_message = "log_group_names needs api, worker, web and migrate."
  }
}

variable "logs_kms_key_arn" {
  description = "CMK for the WAF log group. Null uses CloudWatch's default encryption."
  type        = string
  default     = null
}

variable "cpu_architecture" {
  description = "ARM64 (Graviton, cheaper) or X86_64. Images must be built for it."
  type        = string
  default     = "ARM64"
}

variable "container_insights" {
  description = "ECS Container Insights: enabled, enhanced or disabled."
  type        = string
  default     = "enabled"
}

variable "api_cpu" {
  description = "Fargate CPU units per api task."
  type        = number
  default     = 1024
}

variable "api_memory" {
  description = "Fargate memory (MiB) per api task."
  type        = number
  default     = 2048
}

variable "api_min_count" {
  description = "Minimum (and initial) api task count."
  type        = number
  default     = 2
}

variable "api_max_count" {
  description = "Maximum api task count under autoscaling. Equal to api_min_count disables scaling."
  type        = number
  default     = 6
}

variable "api_ephemeral_gib" {
  description = "Fargate ephemeral storage (GiB, 21 to 200) per api task. SQLite connection uploads and cached SQLite copies (schema discovery, query execution, backup/restore) live under WORK_DIR there. Null: the Fargate default of 20 GiB."
  type        = number
  default     = null
  validation {
    condition     = var.api_ephemeral_gib == null ? true : (var.api_ephemeral_gib >= 21 && var.api_ephemeral_gib <= 200)
    error_message = "api_ephemeral_gib must be null or between 21 and 200."
  }
}

variable "worker_cpu" {
  description = "Fargate CPU units per worker task."
  type        = number
  default     = 512
}

variable "worker_memory" {
  description = "Fargate memory (MiB) per worker task."
  type        = number
  default     = 1024
}

variable "worker_ephemeral_gib" {
  description = "Fargate ephemeral storage (GiB, 21 to 200) per worker task. A backup dump is staged under WORK_DIR before it is uploaded to S3. Null: the Fargate default of 20 GiB."
  type        = number
  default     = 30
  validation {
    condition     = var.worker_ephemeral_gib == null ? true : (var.worker_ephemeral_gib >= 21 && var.worker_ephemeral_gib <= 200)
    error_message = "worker_ephemeral_gib must be null or between 21 and 200."
  }
}

variable "worker_count" {
  description = "Backup worker task count. Several are safe (due schedules are claimed with FOR UPDATE SKIP LOCKED); not autoscaled, raise it when scheduled backups back up."
  type        = number
  default     = 1
  validation {
    condition     = var.worker_count >= 1
    error_message = "worker_count must be at least 1."
  }
}

variable "web_cpu" {
  description = "Fargate CPU units per web task."
  type        = number
  default     = 512
}

variable "web_memory" {
  description = "Fargate memory (MiB) per web task."
  type        = number
  default     = 1024
}

variable "web_min_count" {
  description = "Minimum (and initial) web task count."
  type        = number
  default     = 1
}

variable "web_max_count" {
  description = "Maximum web task count under autoscaling."
  type        = number
  default     = 3
}

variable "cpu_target_percent" {
  description = "Average CPU the autoscaler aims for (api and web)."
  type        = number
  default     = 60
}

variable "idle_timeout" {
  description = "Load balancer idle timeout in seconds. A backup, restore or large export may hold a connection open for a while; this also bounds how long a stalled SQLite upload is kept."
  type        = number
  default     = 120
}

variable "deletion_protection" {
  description = "Block accidental deletion of the load balancer."
  type        = bool
  default     = true
}

variable "access_logs_bucket" {
  description = "Existing bucket for load balancer access logs (needs the ELB log-delivery bucket policy). Null disables."
  type        = string
  default     = null
}

variable "metrics_allowed_cidrs" {
  description = "Source CIDRs allowed to read /metrics through the load balancer. Empty: /metrics answers 404 at the edge."
  type        = list(string)
  default     = []
}

variable "enable_waf" {
  description = "Attach a WAF web ACL (managed rule sets plus a per-IP flood guard) to the load balancer."
  type        = bool
  default     = true
}

variable "waf_rate_limit_per_5min" {
  description = "Requests per source IP per 5 minutes before WAF blocks. Keep it high: SDK backends often sit behind one NAT address."
  type        = number
  default     = 20000
}

variable "waf_log_retention_days" {
  description = "WAF log retention."
  type        = number
  default     = 30
}

variable "source_cidrs" {
  description = "Networks of the databases a team registers, which the api (connection tests, schema discovery, queries) and the worker (scheduled backups) may reach on source_ports. Default: anywhere. Restrict it to your database networks wherever you can."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "source_ports" {
  description = "Ports of the databases a team registers: PostgreSQL 5432, MySQL 3306, SQL Server 1433, MongoDB 27017 and Redis 6379 by default (SQLite connections are uploaded files, not a network target). HTTPS 443 is always open."
  type        = list(number)
  default     = [5432, 3306, 1433, 27017, 6379]
}

variable "ai_egress" {
  description = "Extra egress from the api task for a self-hosted AI provider not on HTTPS 443, as { cidr, port } pairs (for example Ollama or vLLM inside the VPC). Empty: HTTPS only."
  type = list(object({
    cidr = string
    port = number
  }))
  default = []
}

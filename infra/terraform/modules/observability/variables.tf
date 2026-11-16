variable "name_prefix" {
  description = "Prefix for every resource name."
  type        = string
}

variable "log_retention_days" {
  description = "CloudWatch log retention for the app log groups."
  type        = number
  default     = 30
}

variable "logs_kms_key_arn" {
  description = "CMK for the log groups (its key policy must allow CloudWatch Logs, as the kms module's does). Null uses CloudWatch's default encryption."
  type        = string
  default     = null
}

variable "alarm_email" {
  description = "Address subscribed to alarm notifications (confirm the e-mail AWS sends). Null: subscribe something else to the topic."
  type        = string
  default     = null
}

variable "alb_arn_suffix" {
  description = "Load balancer ARN suffix."
  type        = string
}

variable "ecs_cluster_name" {
  description = "ECS cluster name."
  type        = string
}

variable "lb_services" {
  description = "Load-balanced services (service module `services`): { api = { service_name, target_group_arn_suffix }, web = { ... } }. Must contain api."
  type = map(object({
    service_name            = string
    target_group_arn_suffix = string
  }))
  validation {
    condition     = contains(keys(var.lb_services), "api")
    error_message = "lb_services must contain api."
  }
}

variable "ecs_services" {
  description = "Every ECS service to watch for CPU and memory (service module `service_names`): { api = \"api\", worker = \"worker\", web = \"web\" }."
  type        = map(string)
}

variable "db_instance_identifier" {
  description = "RDS instance identifier."
  type        = string
}

variable "db_max_connections" {
  description = "Connection ceiling the alarm measures against."
  type        = number
  default     = 200
}

variable "cache_replication_group_id" {
  description = "ElastiCache replication group id; nodes are <id>-001, <id>-002, ..."
  type        = string
}

variable "cache_node_count" {
  description = "Nodes in the replication group (cache module num_cache_clusters)."
  type        = number
  default     = 1
}

variable "target_5xx_threshold" {
  description = "5xx responses per 5 minutes (per app, and at the load balancer) that raise an alarm."
  type        = number
  default     = 25
}

variable "api_p95_latency_seconds" {
  description = "api p95 time to first byte that raises an alarm. A query, backup, restore or the AI provider's generate/explain call can legitimately run for a while, so keep it generous."
  type        = number
  default     = 30
}

variable "backup_run_log_pattern" {
  description = "CloudWatch Logs JSON filter matching the worker's `scheduled backup` line (dbpilot/worker.py; one per schedule it ran, any status)."
  type        = string
  default     = "{ $.msg = \"scheduled backup\" }"
}

variable "backup_failed_log_pattern" {
  description = "CloudWatch Logs JSON filter matching a scheduled backup that finished with a status other than succeeded."
  type        = string
  default     = "{ ($.msg = \"scheduled backup\") && ($.status != \"succeeded\") }"
}

variable "error_log_pattern" {
  description = "CloudWatch Logs JSON filter matching an error line, applied to the worker log group."
  type        = string
  default     = "{ $.level = \"error\" }"
}

variable "query_refused_log_pattern" {
  description = "CloudWatch Logs JSON filter matching a line the api logs when the SQL guard refuses a query (AC3). Inert (the metric stays at zero) until core/logging.py emits a named line for it; see the main.tf header."
  type        = string
  default     = "{ $.msg = \"query refused\" }"
}

variable "ai_refused_log_pattern" {
  description = "CloudWatch Logs JSON filter matching a line the api logs when the guard refuses an AI-generated statement before execution (AC4). Inert until core/logging.py emits a named line for it; see the main.tf header."
  type        = string
  default     = "{ $.msg = \"ai generation refused\" }"
}

variable "backup_failure_threshold" {
  description = "Failed scheduled backups per 15 minutes that raise an alarm. Some failures are outside the deployment (a connected database down, expired credentials), so this is a rate, not zero."
  type        = number
  default     = 5
}

variable "error_threshold" {
  description = "Worker error lines per 5 minutes that raise an alarm."
  type        = number
  default     = 5
}

variable "query_refusal_threshold" {
  description = "SQL guard refusals per 15 minutes that raise an alarm. A person occasionally marks a statement that is not read-only; a spike can mean someone probing the guard."
  type        = number
  default     = 20
}

variable "ai_refusal_threshold" {
  description = "AI-generation guard refusals per 15 minutes that raise an alarm (informational only: AC4 means an AI-generated write is always refused, never a bypass to investigate)."
  type        = number
  default     = 50
}

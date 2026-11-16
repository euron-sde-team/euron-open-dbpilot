variable "region" {
  description = "AWS region."
  type        = string
}

variable "environment" {
  description = "Environment tag."
  type        = string
  default     = "prod"
}

variable "extra_tags" {
  description = "Tags added to every resource."
  type        = map(string)
  default     = {}
}

variable "name_prefix" {
  description = "Prefix for every resource name (at most 24 characters); the objects bucket name derives from it, so make it globally unique (e.g. \"acme-dbpilot-prod\")."
  type        = string
}

variable "hostname" {
  description = "Public host name of the dashboard and API, e.g. dbpilot.example.com."
  type        = string
}

variable "certificate_arn" {
  description = "ACM certificate in the same region covering hostname."
  type        = string
}

variable "route53_zone_id" {
  description = "Hosted zone for the alias record. Null: CNAME hostname to load_balancer_dns_name yourself."
  type        = string
  default     = null
}

variable "image_tag" {
  description = "Image tag to deploy (the git commit or a release)."
  type        = string
}

variable "alarm_email" {
  description = "Alarm notification address."
  type        = string
  default     = null
}

variable "api_extra_environment" {
  description = "Extra api, worker and migrate environment (see docs/guides/configuration.md). Never secrets."
  type        = map(string)
  default     = {}
}

variable "ai_provider" {
  description = "AI_PROVIDER: the AI assistant's model backend (core/config.py AiProviderKind). \"none\" turns model calls off; explanations and diagnoses then come from the syntax tree and rules alone."
  type        = string
  default     = "none"
  validation {
    condition     = contains(["openai", "anthropic", "none"], var.ai_provider)
    error_message = "ai_provider must be openai, anthropic or none in a deployed environment (never mock, which core/config.py's guard() refuses in production)."
  }
}

variable "ai_base_url" {
  description = "AI_BASE_URL: the AI provider's API base URL (empty for the provider's own default)."
  type        = string
  default     = ""
}

variable "ai_model" {
  description = "AI_MODEL: the model name to call. Required when ai_provider is openai or anthropic (core/config.py's guard())."
  type        = string
  default     = ""
}

variable "db_instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.t4g.medium"
}

variable "cache_node_type" {
  description = "ElastiCache node type."
  type        = string
  default     = "cache.t4g.small"
}

variable "backup_failure_threshold" {
  description = "Failed scheduled backups per 15 minutes that raise an alarm."
  type        = number
  default     = 5
}

variable "query_refusal_threshold" {
  description = "SQL guard refusals per 15 minutes that raise an alarm (a spike can mean someone probing the guard)."
  type        = number
  default     = 20
}

variable "ai_refusal_threshold" {
  description = "AI-generation guard refusals per 15 minutes that raise an alarm (informational: AC4 means these are always refused, never a bypass)."
  type        = number
  default     = 50
}

variable "metrics_allowed_cidrs" {
  description = "Source CIDRs allowed to scrape the api's /metrics through the load balancer."
  type        = list(string)
  default     = []
}

variable "source_cidrs" {
  description = "Networks of the databases a team registers (the api and the worker may reach them on source_ports). Default: anywhere; restrict it to your database CIDRs."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "ai_egress" {
  description = "Extra api egress for a self-hosted AI provider not on HTTPS 443, as { cidr, port } pairs."
  type = list(object({
    cidr = string
    port = number
  }))
  default = []
}

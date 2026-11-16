# Logs, alarms and one dashboard for a deployment:
#
#   - a CloudWatch log group per app (api, worker, web, migrate), encrypted, with retention
#   - alarms to an SNS topic (optionally e-mailed): 5xx, api p95 latency, unhealthy
#     targets, CPU and memory per service, database, cache, and, from metrics extracted
#     from the structured JSON logs, failed scheduled backups and worker loop errors
#   - one dashboard with the same signals side by side
#
# Query execution, AI generation refusals (AC4) and audit log writes do not yet emit a
# distinct, named log line (core/logging.py has no per-event logger for them): the
# query_refused_log_pattern and ai_refused_log_pattern inputs, and the alarms/dashboard
# widgets fed by them, stay inert (zero, never breaching) until the app emits matching
# lines. The worker's own two real lines (dbpilot/worker.py: "scheduled backup" with a
# `status` field, and the exception logged when its loop fails) back real metrics today.
data "aws_region" "current" {}

locals {
  region     = data.aws_region.current.region
  apps       = toset(["api", "worker", "web", "migrate"])
  namespace  = "DBPilot/${var.name_prefix}"
  cache_node = [for i in range(var.cache_node_count) : format("%s-%03d", var.cache_replication_group_id, i + 1)]
}

# ─── Logs ────────────────────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "app" {
  #checkov:skip=CKV_AWS_338:Retention is an input (log_retention_days); aws-prod keeps 365 days.
  for_each          = local.apps
  name              = "/dbpilot/${var.name_prefix}/${each.key}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.logs_kms_key_arn
}

# Log lines turned into metrics (field `msg` of the JSON log lines):
#
#   ScheduledBackups       every scheduled backup the worker ran ("scheduled backup",
#                          dbpilot/worker.py), any status
#   ScheduledBackupFailures scheduled backups that finished with status != succeeded
#   WorkerErrors           error lines from the worker (its tick loop failing)
#   QueryRefusals          queries the SQL guard refused (api): inert until core/logging.py
#                          emits a named line for it (see the header)
#   AiRefusals             AI-generated statements the guard refused before execution
#                          (AC4): inert until core/logging.py emits a named line for it
locals {
  log_metrics = {
    "ScheduledBackups-worker"        = { metric = "ScheduledBackups", group = "worker", pattern = var.backup_run_log_pattern, value = "1" }
    "ScheduledBackupFailures-worker" = { metric = "ScheduledBackupFailures", group = "worker", pattern = var.backup_failed_log_pattern, value = "1" }
    "WorkerErrors-worker"            = { metric = "WorkerErrors", group = "worker", pattern = var.error_log_pattern, value = "1" }
    "QueryRefusals-api"              = { metric = "QueryRefusals", group = "api", pattern = var.query_refused_log_pattern, value = "1" }
    "AiRefusals-api"                 = { metric = "AiRefusals", group = "api", pattern = var.ai_refused_log_pattern, value = "1" }
  }
}

resource "aws_cloudwatch_log_metric_filter" "this" {
  for_each       = local.log_metrics
  name           = "${var.name_prefix}-${each.key}"
  log_group_name = aws_cloudwatch_log_group.app[each.value.group].name
  pattern        = each.value.pattern
  metric_transformation {
    name      = each.value.metric
    namespace = local.namespace
    value     = each.value.value
    unit      = "Count"
  }
}

# ─── Alarms ──────────────────────────────────────────────────────────────────

resource "aws_sns_topic" "alarms" {
  name              = "${var.name_prefix}-alarms"
  kms_master_key_id = "alias/aws/sns"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alarm_email == null ? 0 : 1
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

locals {
  lb_alarms = merge([
    for name, s in var.lb_services : {
      "${name}-5xx" = {
        description = "${name}: 5xx responses from the tasks"
        namespace   = "AWS/ApplicationELB"
        metric      = "HTTPCode_Target_5XX_Count"
        stat        = "Sum"
        threshold   = var.target_5xx_threshold
        period      = 300
        dimensions  = { LoadBalancer = var.alb_arn_suffix, TargetGroup = s.target_group_arn_suffix }
        comparison  = "GreaterThanThreshold"
      }
      "${name}-unhealthy" = {
        description = "${name}: tasks failing load balancer health checks"
        namespace   = "AWS/ApplicationELB"
        metric      = "UnHealthyHostCount"
        stat        = "Maximum"
        threshold   = 0
        period      = 60
        dimensions  = { LoadBalancer = var.alb_arn_suffix, TargetGroup = s.target_group_arn_suffix }
        comparison  = "GreaterThanThreshold"
      }
    }
  ]...)

  ecs_alarms = merge([
    for name, service_name in var.ecs_services : {
      "${name}-cpu" = {
        description = "${name}: CPU sustained above 85%"
        namespace   = "AWS/ECS"
        metric      = "CPUUtilization"
        stat        = "Average"
        threshold   = 85
        period      = 300
        dimensions  = { ClusterName = var.ecs_cluster_name, ServiceName = service_name }
        comparison  = "GreaterThanThreshold"
      }
      "${name}-memory" = {
        description = "${name}: memory sustained above 85%"
        namespace   = "AWS/ECS"
        metric      = "MemoryUtilization"
        stat        = "Average"
        threshold   = 85
        period      = 300
        dimensions  = { ClusterName = var.ecs_cluster_name, ServiceName = service_name }
        comparison  = "GreaterThanThreshold"
      }
    }
  ]...)

  edge_alarms = {
    alb-5xx = {
      description = "Load balancer 5xx (no healthy target, timeouts)"
      namespace   = "AWS/ApplicationELB"
      metric      = "HTTPCode_ELB_5XX_Count"
      stat        = "Sum"
      threshold   = var.target_5xx_threshold
      period      = 300
      dimensions  = { LoadBalancer = var.alb_arn_suffix }
      comparison  = "GreaterThanThreshold"
    }
  }

  db_alarms = {
    db-cpu = {
      description = "Database CPU sustained above 80%"
      namespace   = "AWS/RDS"
      metric      = "CPUUtilization"
      stat        = "Average"
      threshold   = 80
      period      = 300
      dimensions  = { DBInstanceIdentifier = var.db_instance_identifier }
      comparison  = "GreaterThanThreshold"
    }
    db-storage = {
      description = "Database free storage below 5 GiB"
      namespace   = "AWS/RDS"
      metric      = "FreeStorageSpace"
      stat        = "Minimum"
      threshold   = 5 * 1024 * 1024 * 1024
      period      = 300
      dimensions  = { DBInstanceIdentifier = var.db_instance_identifier }
      comparison  = "LessThanThreshold"
    }
    db-connections = {
      description = "Database connections above 80% of the configured ceiling"
      namespace   = "AWS/RDS"
      metric      = "DatabaseConnections"
      stat        = "Maximum"
      threshold   = var.db_max_connections * 0.8
      period      = 300
      dimensions  = { DBInstanceIdentifier = var.db_instance_identifier }
      comparison  = "GreaterThanThreshold"
    }
  }

  # Per node: the cache holds rate limits, the schema cache and the result page cache; a
  # full node evicts or refuses writes, which shows up as slower pages and more schema
  # rediscovery.
  # Keys use the node number (known at plan time); the node id, which embeds the
  # replication group id and is only known after apply, goes in the values.
  cache_alarms = merge([
    for i, node in local.cache_node : {
      "cache-node-${i + 1}-memory" = {
        description = "Cache node ${node}: memory above 80%"
        namespace   = "AWS/ElastiCache"
        metric      = "DatabaseMemoryUsagePercentage"
        stat        = "Maximum"
        threshold   = 80
        period      = 300
        dimensions  = { CacheClusterId = node }
        comparison  = "GreaterThanThreshold"
      }
      "cache-node-${i + 1}-cpu" = {
        description = "Cache node ${node}: engine CPU above 80%"
        namespace   = "AWS/ElastiCache"
        metric      = "EngineCPUUtilization"
        stat        = "Average"
        threshold   = 80
        period      = 300
        dimensions  = { CacheClusterId = node }
        comparison  = "GreaterThanThreshold"
      }
    }
  ]...)

  threshold_alarms = merge(local.lb_alarms, local.ecs_alarms, local.edge_alarms, local.db_alarms, local.cache_alarms)
}

resource "aws_cloudwatch_metric_alarm" "threshold" {
  for_each            = local.threshold_alarms
  alarm_name          = "${var.name_prefix}-${each.key}"
  alarm_description   = each.value.description
  namespace           = each.value.namespace
  metric_name         = each.value.metric
  statistic           = each.value.stat
  dimensions          = each.value.dimensions
  period              = each.value.period
  evaluation_periods  = 2
  threshold           = each.value.threshold
  comparison_operator = each.value.comparison
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
}

# p95, not average: a slow connected database hides inside an average of cached reads.
# Some api routes legitimately wait on a query, a backup/restore or the AI provider's
# generate/explain call, so the threshold is a coarse ceiling: crossing it means a
# connected database or the AI provider is badly slow, or the api itself is saturated.
resource "aws_cloudwatch_metric_alarm" "api_latency_p95" {
  alarm_name          = "${var.name_prefix}-api-latency-p95"
  alarm_description   = "api: p95 response time above ${var.api_p95_latency_seconds}s (a connected database or the AI provider slow, or the api saturated)"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "TargetResponseTime"
  extended_statistic  = "p95"
  dimensions          = { LoadBalancer = var.alb_arn_suffix, TargetGroup = var.lb_services["api"].target_group_arn_suffix }
  period              = 300
  evaluation_periods  = 3
  threshold           = var.api_p95_latency_seconds
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
}

# Alarms on the metrics extracted from the logs (see the top of the file). Sums over one
# window each: 5 minutes for failures and errors, 15 minutes for refusals. The two
# refusal alarms stay inert (never breaching) until core/logging.py emits their named
# log lines (see the header comment).
locals {
  log_alarms = {
    scheduled-backups-failed = {
      description = "More than ${var.backup_failure_threshold} scheduled backups failed in 15 minutes (the connected database unreachable, credentials expired, or a worker fault)"
      metric      = "ScheduledBackupFailures"
      threshold   = var.backup_failure_threshold
      period      = 900
    }
    worker-errors = {
      description = "The worker logged more than ${var.error_threshold} errors in 5 minutes"
      metric      = "WorkerErrors"
      threshold   = var.error_threshold
      period      = 300
    }
    query-refusals = {
      description = "The SQL guard refused more than ${var.query_refusal_threshold} queries in 15 minutes: someone may be probing the guard, or a human keeps marking non-read-only statements as a query"
      metric      = "QueryRefusals"
      threshold   = var.query_refusal_threshold
      period      = 900
    }
    ai-refusals = {
      description = "The AI assistant's own guard refused more than ${var.ai_refusal_threshold} generated statements in 15 minutes (AC4: an AI-generated statement never writes, so a spike here is expected under load, not a bypass)"
      metric      = "AiRefusals"
      threshold   = var.ai_refusal_threshold
      period      = 900
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "logs" {
  for_each            = local.log_alarms
  alarm_name          = "${var.name_prefix}-${each.key}"
  alarm_description   = each.value.description
  namespace           = local.namespace
  metric_name         = each.value.metric
  statistic           = "Sum"
  period              = each.value.period
  evaluation_periods  = 1
  threshold           = each.value.threshold
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
}

# ─── Dashboard ───────────────────────────────────────────────────────────────

resource "aws_cloudwatch_dashboard" "this" {
  dashboard_name = var.name_prefix
  dashboard_body = jsonencode({
    widgets = concat(
      [
        {
          type = "metric", x = 0, y = 0, width = 12, height = 6
          properties = {
            title = "Requests and errors", region = local.region, stat = "Sum", period = 60
            metrics = [
              ["AWS/ApplicationELB", "RequestCount", "LoadBalancer", var.alb_arn_suffix],
              [".", "HTTPCode_Target_4XX_Count", ".", "."],
              [".", "HTTPCode_Target_5XX_Count", ".", "."],
              [".", "HTTPCode_ELB_5XX_Count", ".", "."],
            ]
          }
        },
        {
          type = "metric", x = 12, y = 0, width = 12, height = 6
          properties = {
            title = "api latency p50 / p95 / p99", region = local.region, period = 60
            metrics = [
              ["AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", var.alb_arn_suffix, "TargetGroup", var.lb_services["api"].target_group_arn_suffix, { stat = "p50" }],
              ["...", { stat = "p95" }],
              ["...", { stat = "p99" }],
            ]
          }
        },
        {
          type = "metric", x = 0, y = 12, width = 8, height = 6
          properties = {
            title = "Scheduled backups / failures / worker errors / SQL and AI refusals", region = local.region, period = 60, stat = "Sum"
            metrics = [
              [local.namespace, "ScheduledBackups"],
              [local.namespace, "ScheduledBackupFailures"],
              [local.namespace, "WorkerErrors"],
              [local.namespace, "QueryRefusals"],
              [local.namespace, "AiRefusals"],
            ]
          }
        },
        {
          type = "metric", x = 8, y = 12, width = 8, height = 6
          properties = {
            title = "Database", region = local.region, stat = "Average", period = 60
            metrics = [
              ["AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", var.db_instance_identifier],
              [".", "DatabaseConnections", ".", "."],
              [".", "ReadLatency", ".", "."],
              [".", "WriteLatency", ".", "."],
            ]
          }
        },
        {
          type = "metric", x = 16, y = 12, width = 8, height = 6
          properties = {
            title = "Cache memory % / engine CPU %", region = local.region, stat = "Maximum", period = 60
            metrics = concat(
              [for node in local.cache_node : ["AWS/ElastiCache", "DatabaseMemoryUsagePercentage", "CacheClusterId", node]],
              [for node in local.cache_node : ["AWS/ElastiCache", "EngineCPUUtilization", "CacheClusterId", node]],
            )
          }
        },
      ],
      [
        for i, name in sort(keys(var.ecs_services)) : {
          type = "metric", x = (i % 4) * 6, y = 6 + floor(i / 4) * 18, width = 6, height = 6
          properties = {
            title = "${name}: CPU / memory", region = local.region, stat = "Average", period = 60
            metrics = [
              ["AWS/ECS", "CPUUtilization", "ClusterName", var.ecs_cluster_name, "ServiceName", var.ecs_services[name]],
              [".", "MemoryUtilization", ".", ".", ".", "."],
            ]
          }
        }
      ],
    )
  })
}

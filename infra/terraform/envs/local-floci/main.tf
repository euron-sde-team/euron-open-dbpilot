# ─────────────────────────────────────────────────────────────────────────────
# Local environment on the Floci AWS emulator.
#
# The modules that build production run here unchanged; only the provider endpoints
# differ. `make up` applies this automatically (the compose.floci.yaml `terraform`
# one-shot) before migrate, api and worker start, so local development uses S3 and KMS
# exactly as AWS does:
#
#   terraform -chdir=infra/terraform/envs/local-floci init
#   terraform -chdir=infra/terraform/envs/local-floci apply -var endpoint=http://localhost:5866
#
# Postgres and Redis are plain Compose containers locally (the emulator does not back RDS
# or ElastiCache; see deploy/compose/compose.yaml). DBPilot has no mail code path (0.1.0
# never sends mail; see docs/PLAN.md), so there is no email module here, unlike a sibling
# project that uses SES. The database, cache, network, service, ecr and observability
# modules are exercised by aws-dev and aws-prod (make tf-validate, make tf-aws-on-floci).
# ─────────────────────────────────────────────────────────────────────────────

module "kms" {
  source                  = "../../modules/kms"
  name_prefix             = var.name_prefix
  deletion_window_in_days = 7
}

module "storage" {
  source        = "../../modules/storage"
  name_prefix   = var.name_prefix
  kms_key_arn   = module.kms.key_arn
  enforce_tls   = false # the emulator speaks plain HTTP
  force_destroy = true
  cors_origins  = var.cors_origins
}

# Generated secrets are an AWS-deployment concern; locally compose reads
# deploy/compose/.env (make env: SESSION_SECRET, LOCAL_MASTER_KEY). Created here anyway
# so the module is exercised, and so Secrets Manager has something real behind it.
module "secrets" {
  source                  = "../../modules/secrets"
  name_prefix             = var.name_prefix
  kms_key_arn             = module.kms.key_arn
  recovery_window_in_days = 0
}

output "objects_bucket" {
  description = "OBJECTS_BUCKET for the local stack."
  value       = module.storage.bucket_name
}

output "kms_key_alias" {
  description = "KMS_KEY_ID for the local stack: the connection-secret and AI-key sealing key under KEY_MODE=kms."
  value       = module.kms.alias_name
}

output "app_secret_arn" {
  description = "The emulated Secrets Manager secret (SESSION_SECRET)."
  value       = module.secrets.secret_arn
}

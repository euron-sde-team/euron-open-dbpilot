#!/usr/bin/env bash
# Write deploy/compose/.env with random, local-only secrets (once; never overwrites).
set -euo pipefail
f="$(cd "$(dirname "$0")/.." && pwd)/deploy/compose/.env"
if [[ -f "$f" ]]; then
  echo "deploy/compose/.env exists (delete it to regenerate)"
  exit 0
fi
rand() { openssl rand -base64 48 | tr -d '/+=\n' | cut -c1-"$1"; }
umask 077
# The signing secrets must differ (the API refuses to boot otherwise); independent draws
# of 64 characters always do. The demo password has upper and lower case letters and a
# digit, as the API's password policy requires. SQL Server's SA password needs upper,
# lower, a digit and a symbol, at least 8 characters: the fixed prefix and suffix here
# guarantee that regardless of what the random middle draws.
cat > "$f" <<ENV
SESSION_SECRET=$(rand 64)
LOCAL_MASTER_KEY=$(rand 64)
DBPILOT_DEMO_PASSWORD=Pilot-$(rand 10)-7
DBPILOT_SOURCE_ADMIN_PASSWORD=$(rand 24)
DBPILOT_READER_PASSWORD=Reader-$(rand 10)-3
DBPILOT_MSSQL_SA_PASSWORD=Dbp-$(rand 16)-9!
ENV
echo "wrote deploy/compose/.env"

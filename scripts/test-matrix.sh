#!/usr/bin/env bash
# Run the API integration suite under every backend combination DBPilot's own code
# actually branches on in 0.1.0 (see core/storage.py's ObjectStorage protocol and
# core/sealing.py's KeySealer protocol):
#
#   storage: local disk | S3 (on Floci)
#   key_mode: local (AES-256-GCM, LOCAL_MASTER_KEY) | kms (AWS KMS Encrypt/Decrypt, on Floci)
#
# local x local needs only Postgres, Redis and the mock AI/LLM service (test-db.sh).
# s3 x kms needs Floci with the local-floci Terraform applied (the objects bucket and the
# KMS key), which `make up` already does; this script applies it standalone when Floci is
# not already up, so `make test-matrix` works on its own. 4 combinations, each run against
# its own test database so no run can see another's state.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/deploy/compose/.env"
COMPOSE_DIR="$ROOT/deploy/compose"
port() { local v; v="$(grep -s "^$1=" "$ENV_FILE" | cut -d= -f2 || true)"; echo "${v:-$2}"; }
PG="postgresql+asyncpg://dbpilot:dbpilot@localhost:$(port DBPILOT_PG_PORT 5842)"
REDIS_PORT="$(port DBPILOT_REDIS_PORT 5879)"
FLOCI_PORT="$(port DBPILOT_FLOCI_PORT 5866)"

"$ROOT/scripts/test-db.sh"

export TEST_MOCKAI_URL="http://localhost:$(port DBPILOT_MOCK_AI_PORT 5850)"
export TEST_REDIS_URL="redis://localhost:${REDIS_PORT}/14"
# The demo source databases' real admin logins (deploy/compose/.env), not conftest.py's
# own fallback defaults: the source containers were started with the real passwords from
# .env, so a test run that does not pass these fails every connector test with a real
# authentication error, not a skip.
export TEST_SOURCE_ADMIN_PASSWORD="$(port DBPILOT_SOURCE_ADMIN_PASSWORD test-only-source-admin-pass-1)"
export TEST_MSSQL_SA_PASSWORD="$(port DBPILOT_MSSQL_SA_PASSWORD Dbp-Test-Sql-Server-1)"
# boto3 (core/aws.py) reads credentials from the standard chain; Floci accepts any values,
# but something must be present or every KMS/S3 call fails with NoCredentialsError before
# it ever reaches the emulator (compose.floci.yaml sets the same placeholder pair for the
# app containers).
export AWS_ACCESS_KEY_ID="test"
export AWS_SECRET_ACCESS_KEY="test"
export AWS_REGION="us-east-1"

# Docker is the only host prerequisite (no host psql assumed): create each matrix
# database through the already-running postgres container itself.
PG_CONTAINER="$(cd "$ROOT" && docker compose --env-file "$ENV_FILE" -f deploy/compose/compose.yaml ps -q postgres)"

ready() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }
if ! ready "$FLOCI_PORT"; then
  echo "starting floci (for storage=s3, key_mode=kms)"
  docker compose --env-file "$ENV_FILE" -f "$ROOT/deploy/compose/compose.yaml" \
    -f "$ROOT/deploy/compose/compose.floci.yaml" up -d --wait floci terraform
fi

failed=()
runs=0
for storage in local s3; do
  for key_mode in local kms; do
    runs=$((runs + 1))
    echo
    echo "== storage=$storage key_mode=$key_mode"
    db="dbpilot_test_matrix_${storage}_${key_mode}"
    docker exec -e PGPASSWORD=dbpilot "$PG_CONTAINER" \
      psql -U dbpilot -d dbpilot -v ON_ERROR_STOP=1 \
      -c "DROP DATABASE IF EXISTS ${db};" -c "CREATE DATABASE ${db};" >/dev/null
    aws_endpoint=""; [[ "$storage" == s3 || "$key_mode" == kms ]] && aws_endpoint="http://localhost:${FLOCI_PORT}"
    if ! (cd "$ROOT/apps/api" && \
          TEST_DATABASE_URL="$PG/${db}" \
          TEST_STORAGE="$storage" TEST_KEY_MODE="$key_mode" TEST_AWS_ENDPOINT="$aws_endpoint" \
          uv run pytest tests/integration -m integration -q -p no:warnings -rf); then
      failed+=("storage=$storage key_mode=$key_mode")
    fi
  done
done
echo
if ((${#failed[@]})); then
  printf 'matrix: FAILED for %s\n' "${failed[@]}"
  exit 1
fi
echo "matrix: all $runs combinations passed"

#!/usr/bin/env bash
# Make sure the services the integration suite talks to are reachable: Postgres, Redis,
# the mock AI service and the five demo source databases (postgres, mysql, mssql, mongo,
# redis) the connector-layer tests connect to.
# Reuses the Compose services when the stack is up; otherwise starts just those. Tests
# never use a host-installed Postgres, Redis or any other database engine.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/deploy/compose/.env"
port() { local v; v="$(grep -s "^$1=" "$ENV_FILE" | cut -d= -f2 || true)"; echo "${v:-$2}"; }

ready() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

missing=()
ready "$(port DBPILOT_PG_PORT 5842)" || missing+=(postgres)
ready "$(port DBPILOT_REDIS_PORT 5879)" || missing+=(redis)
ready "$(port DBPILOT_MOCK_AI_PORT 5850)" || missing+=(mock-ai)
ready "$(port DBPILOT_SOURCE_PG_PORT 5843)" || missing+=(source-postgres)
ready "$(port DBPILOT_SOURCE_MYSQL_PORT 5806)" || missing+=(source-mysql)
ready "$(port DBPILOT_SOURCE_MSSQL_PORT 5807)" || missing+=(source-mssql)
ready "$(port DBPILOT_SOURCE_MONGO_PORT 5808)" || missing+=(source-mongo)
ready "$(port DBPILOT_SOURCE_REDIS_PORT 5809)" || missing+=(source-redis)
((${#missing[@]})) || exit 0

"$ROOT/scripts/gen-local-env.sh" >/dev/null
echo "starting ${missing[*]}"
docker compose --env-file "$ENV_FILE" -f "$ROOT/deploy/compose/compose.yaml" \
  up -d --wait "${missing[@]}"

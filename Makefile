# Euron Open DBPilot: developer entry points. `make` lists every target.
SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

COMPOSE_DIR := deploy/compose
FLOCI       := docker compose --env-file $(COMPOSE_DIR)/.env -f $(COMPOSE_DIR)/compose.yaml -f $(COMPOSE_DIR)/compose.floci.yaml
AWS_LOCAL   := docker compose --env-file $(COMPOSE_DIR)/.env -f $(COMPOSE_DIR)/compose.yaml -f $(COMPOSE_DIR)/compose.aws.yaml
API         := cd apps/api &&
WEB         := cd apps/web &&
TF_IMAGE    := hashicorp/terraform:1.16.6
TF_ENVS     := local-floci aws-dev aws-prod
# Use a local terraform when installed, otherwise the pinned container (providers cached
# in a named volume, so repeated runs do not download them again).
TF          := $(shell command -v terraform >/dev/null 2>&1 && echo terraform || echo docker run --rm -v $(CURDIR)/infra:/infra -v dbpilot-tf-plugins:/plugins -e TF_PLUGIN_CACHE_DIR=/plugins -w /infra/terraform $(TF_IMAGE))
TF_ROOT     := $(shell command -v terraform >/dev/null 2>&1 && echo infra/terraform || echo /infra/terraform)
ENV         ?= aws-dev
# Host ports, overridable in deploy/compose/.env (the same variables compose reads;
# defaults match docs/PLAN.md "Ports (base 5800; project 18)").
envport      = $(or $(shell grep -s '^$(1)=' $(COMPOSE_DIR)/.env | cut -d= -f2),$(2))
WEB_PORT     = $(call envport,DBPILOT_WEB_PORT,5810)
API_PORT     = $(call envport,DBPILOT_API_PORT,5830)
PG_PORT      = $(call envport,DBPILOT_PG_PORT,5842)
REDIS_PORT   = $(call envport,DBPILOT_REDIS_PORT,5879)
FLOCI_PORT   = $(call envport,DBPILOT_FLOCI_PORT,5866)
MAIL_PORT    = $(call envport,DBPILOT_MAILPIT_PORT,5825)
MOCK_AI_PORT = $(call envport,DBPILOT_MOCK_AI_PORT,5850)
SOURCE_PG_PORT    = $(call envport,DBPILOT_SOURCE_PG_PORT,5843)
SOURCE_MYSQL_PORT = $(call envport,DBPILOT_SOURCE_MYSQL_PORT,5806)
SOURCE_MSSQL_PORT = $(call envport,DBPILOT_SOURCE_MSSQL_PORT,5807)
SOURCE_MONGO_PORT = $(call envport,DBPILOT_SOURCE_MONGO_PORT,5808)
SOURCE_REDIS_PORT = $(call envport,DBPILOT_SOURCE_REDIS_PORT,5809)
# The seeded demo login's password (published, not a secret); the seed, e2e and qa use
# it. Must match `dbpilot seed`'s own DEMO_PASSWORD env read, or `make demo` seeds one
# password while `make e2e`/`make qa` sign in with another.
DEMO_PW      = $(or $(shell grep -s '^DBPILOT_DEMO_PASSWORD=' $(COMPOSE_DIR)/.env | cut -d= -f2),Demo-Owner-Pass-1)
# The integration suite's own database, Redis index, mock AI and demo-source logins
# (never the app's own).
TEST_ENV     = TEST_DATABASE_URL=postgresql+asyncpg://dbpilot:dbpilot@localhost:$(PG_PORT)/dbpilot_test \
               TEST_REDIS_URL=redis://localhost:$(REDIS_PORT)/15 \
               TEST_MOCKAI_URL=http://localhost:$(MOCK_AI_PORT) \
               TEST_SOURCE_HOST=127.0.0.1 \
               TEST_SOURCE_PG_PORT=$(SOURCE_PG_PORT) TEST_SOURCE_MYSQL_PORT=$(SOURCE_MYSQL_PORT) \
               TEST_SOURCE_MSSQL_PORT=$(SOURCE_MSSQL_PORT) TEST_SOURCE_MONGO_PORT=$(SOURCE_MONGO_PORT) \
               TEST_SOURCE_REDIS_PORT=$(SOURCE_REDIS_PORT) \
               TEST_SOURCE_ADMIN_PASSWORD=$(or $(shell grep -s '^DBPILOT_SOURCE_ADMIN_PASSWORD=' $(COMPOSE_DIR)/.env | cut -d= -f2),test-only-source-admin-pass-1) \
               TEST_MSSQL_SA_PASSWORD=$(or $(shell grep -s '^DBPILOT_MSSQL_SA_PASSWORD=' $(COMPOSE_DIR)/.env | cut -d= -f2),Dbp-Test-Sql-Server-1) \
               TEST_AWS_ENDPOINT=http://localhost:$(FLOCI_PORT) TEST_KMS_KEY_ID=alias/dbpilot-local

.PHONY: help env up up-aws down logs ps demo test test-integration test-matrix \
        e2e qa screenshots lint typecheck build openapi sanitize \
        tf-fmt tf-validate tf-lint tf-security tf-aws-on-floci clean

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage: make \033[36m<target>\033[0m\n\n"} /^[a-zA-Z0-9_-]+:.*##/ { printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

# --- Local ---------------------------------------------------------------------

env: ## Generate deploy/compose/.env with random local-only secrets
	@scripts/gen-local-env.sh

up: env ## Start everything on emulated AWS (Floci), provisioned by the same Terraform as production
	$(FLOCI) up -d --build --wait
	@echo ""
	@echo "  dashboard     http://localhost:$(WEB_PORT)"
	@echo "  api docs      http://localhost:$(API_PORT)/docs"
	@echo "  mail          http://localhost:$(MAIL_PORT)   (Mailpit)"
	@echo "  mock AI       http://localhost:$(MOCK_AI_PORT)"
	@echo ""
	@echo "  Next: make demo   (a seeded organisation: projects, connections, queries, backups, audit history)"

up-aws: env ## Start the stack against real AWS resources (needs deploy/compose/.env.aws)
	@test -f $(COMPOSE_DIR)/.env.aws || { echo "copy $(COMPOSE_DIR)/.env.aws.example to .env.aws first"; exit 1; }
	$(AWS_LOCAL) up -d --build --wait

demo: ## Seed the demo organisation, create a demo instance administrator, and print the logins
	$(FLOCI) exec -T api dbpilot seed
	@DBPILOT_ADMIN_PASSWORD='$(DEMO_PW)' $(FLOCI) exec -T -e DBPILOT_ADMIN_PASSWORD='$(DEMO_PW)' api dbpilot create-admin --email qa-instance-admin@lumenlogistics.example --name "Instance Admin" >/dev/null
	@echo ""
	@echo "  Sign in at http://localhost:$(WEB_PORT) with a login above"
	@echo "  Instance admin (for /admin/system): qa-instance-admin@lumenlogistics.example, same password"

down: ## Stop the stack and delete its data (the Terraform provider cache survives)
	-$(FLOCI) down --remove-orphans
	-docker volume rm dbpilot_pgdata dbpilot_sourcepg dbpilot_sourcemysql dbpilot_sourcemssql dbpilot_sourcemongo dbpilot_objects dbpilot_work 2>/dev/null
	-$(AWS_LOCAL) down --remove-orphans 2>/dev/null

logs: ## Tail logs (SERVICE=api|web|worker|mock-ai|migrate|postgres|redis)
	$(FLOCI) logs -f --tail 100 $(SERVICE)

ps: ## Show stack status
	$(FLOCI) ps

# --- Quality ---------------------------------------------------------------------

lint: ## Lint the API (ruff) and the web app (eslint)
	$(API) uv run ruff check src tests && uv run ruff format --check src tests
	$(WEB) pnpm lint

typecheck: ## Typecheck the API (mypy --strict) and the web app (tsc)
	$(API) uv run mypy --strict src
	$(WEB) pnpm typecheck

test: ## Unit tests: API (pytest) and web (vitest). No database, no network
	$(API) uv run pytest tests/unit -q
	$(WEB) pnpm test -- --run

test-integration: ## API integration tests against the Compose Postgres, Redis, mock AI and the demo source databases (started if needed)
	@scripts/test-db.sh
	$(API) $(TEST_ENV) uv run pytest tests/integration -m integration -q --cov --cov-report=term-missing:skip-covered

test-matrix: ## Integration tests under every backend combination DBPilot branches on: storage local|S3, keys local|KMS (via Floci)
	@scripts/test-matrix.sh

e2e: ## Browser end-to-end tests (Playwright) against the running stack (make up && make demo)
	cd e2e && pnpm install --frozen-lockfile && pnpm exec playwright install chromium && \
	  WEB_URL=http://localhost:$(WEB_PORT) API_URL=http://localhost:$(API_PORT) MAILPIT_URL=http://localhost:$(MAIL_PORT) \
	  DBPILOT_DEMO_PASSWORD='$(DEMO_PW)' E2E_PASSWORD='$(DEMO_PW)' pnpm test

qa: ## Browser QA sweep (Puppeteer): every page, light and dark, desktop and phone
	cd scripts/qa && pnpm install --frozen-lockfile && \
	  QA_BASE=http://localhost:$(WEB_PORT) QA_MAILPIT=http://localhost:$(MAIL_PORT) \
	  DBPILOT_DEMO_PASSWORD='$(DEMO_PW)' QA_PASSWORD='$(DEMO_PW)' node qa.mjs

screenshots: ## Curated screenshots (docs/assets/screenshots) and the tour GIF, from the seeded stack
	cd scripts/qa && pnpm install --frozen-lockfile && \
	  QA_BASE=http://localhost:$(WEB_PORT) QA_MAILPIT=http://localhost:$(MAIL_PORT) \
	  DBPILOT_DEMO_PASSWORD='$(DEMO_PW)' QA_PASSWORD='$(DEMO_PW)' node screenshots.mjs && ./make-tour.sh

build: ## Build the container images (server, web)
	$(FLOCI) build

openapi: ## Regenerate docs/api/openapi.yaml from the FastAPI app
	@mkdir -p docs/api
	$(API) uv run dbpilot openapi > ../../docs/api/openapi.yaml

sanitize: ## Scan committable files for credentials, cloud identifiers and em/en dashes
	@scripts/check-sanitization.sh

# --- Terraform ---------------------------------------------------------------------

tf-fmt: ## Format all Terraform
	$(TF) fmt -recursive $(TF_ROOT)

tf-validate: ## terraform validate every environment (no backend, no credentials)
	@for env in $(TF_ENVS); do \
	  echo "== $$env"; \
	  $(TF) -chdir=$(TF_ROOT)/envs/$$env init -backend=false -input=false -no-color >/dev/null && \
	  $(TF) -chdir=$(TF_ROOT)/envs/$$env validate -no-color || exit 1; \
	done

tf-lint: ## tflint every module and environment (plugins cached in a docker volume)
	docker run --rm -v $(CURDIR)/infra:/infra -v dbpilot-tflint:/plugins -e TFLINT_PLUGIN_DIR=/plugins -w /infra/terraform ghcr.io/terraform-linters/tflint:v0.64.0 --init
	docker run --rm -v $(CURDIR)/infra:/infra -v dbpilot-tflint:/plugins -e TFLINT_PLUGIN_DIR=/plugins -w /infra/terraform ghcr.io/terraform-linters/tflint:v0.64.0 --recursive --format compact

tf-security: ## checkov policy scan of the Terraform (any unskipped finding fails; every skip carries its reason inline)
	docker run --rm -v $(CURDIR)/infra:/infra -w /infra/terraform bridgecrew/checkov:3.3.30 -d . --config-file .checkov.yaml

tf-aws-on-floci: ## Plan, apply, re-plan and destroy the full aws-dev environment against a throwaway Floci (port 5896)
	@scripts/tf-aws-on-floci.sh

clean: ## Remove build output and caches
	rm -rf apps/web/.next apps/api/.pytest_cache apps/api/.mypy_cache apps/api/.ruff_cache apps/api/.coverage \
	  e2e/test-results e2e/playwright-report scripts/qa/output reports

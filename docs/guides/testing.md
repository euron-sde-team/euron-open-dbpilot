# Testing

Every gate below runs locally against Docker; none of it touches a real AWS account. Real
counts from the most recent full run (2026-11-16) are given so this page says what the
suite actually proves rather than what it is supposed to prove.

## Static

```sh
make lint        # ruff check + ruff format --check (api), eslint (web)
make typecheck    # mypy --strict (api), tsc --noEmit (web)
```

Most recent run: ruff clean (191 files already formatted), mypy strict clean across 131
source files, eslint and tsc both clean.

## Unit tests

```sh
make test    # pytest tests/unit (api), vitest (web)
```

No network, no database. Most recent run: **206 backend tests passed**; **65 web unit
tests passed**.

## Integration tests

```sh
make test-integration
```

Runs `pytest tests/integration -m integration` against the real Postgres, Redis, mock AI
service and the five demo source databases in the Compose stack (started automatically if
not already running), covering every endpoint, access control (including the AC9
404-not-403 rule), read-only enforcement per engine, the guard, confirmations and the
audit trail. Most recent run: **113 tests passed, 83% line coverage** on
`apps/api/src`.

## Test matrix

```sh
make test-matrix
```

Runs the integration suite under every backend combination DBPilot's own code actually
branches on in 0.1.0: `storage=local|s3` x `key_mode=local|kms` (`core/storage.py`'s
`ObjectStorage` protocol, `core/sealing.py`'s `KeySealer` protocol), the `s3`/`kms` legs
against Floci. **4 of 4 combinations passed.**

## OpenAPI drift

```sh
make openapi
```

Regenerates `docs/api/openapi.yaml` from the live FastAPI app (`dbpilot openapi`).
Committed output must match a fresh export; a mismatch means a route or schema changed
without the docs being regenerated.

## End-to-end (Playwright)

```sh
make e2e
```

Against `make up && make demo`'s full stack: signing in and out, the connections list
with no secret anywhere on the page, opening a connection's real schema and weak-config
check, a read-only query that pages, a query refused for exceeding the scan limit, the AI
assistant refusing a generated write with no override control anywhere on the page, the
AI assistant generating a genuine read statement, a human-marked write going through the
full two-step confirmation and producing all three audit entries (`write.requested`,
`write.confirmed`, `write.executed`), the schema explorer and relationship view, MongoDB
getting its own panel instead of a SQL editor, the backups page and a real manual backup
with a passing integrity check, the audit log, and a project member with no grant getting
a 404, not a 403, on a connection. **15 tests, run clean on 3 consecutive runs** on the
most recent verification pass.

## Browser QA (Puppeteer)

```sh
make qa
```

Visits every dashboard page (anonymous login, project owner, project admin, project
viewer, and a genuine instance administrator for `/admin/system`, created through the real
`dbpilot create-admin` CLI the same way an operator would) in light and dark mode at
desktop and phone width, and fails on console errors, failed or 4xx/5xx network requests,
broken images, horizontal overflow, or an unexplained empty state. Most recent run:
**96 page views, 0 problems.**

## Infrastructure

```sh
make tf-validate     # terraform validate, every env, no backend/credentials needed
make tf-lint         # tflint, every module and env
make tf-security     # checkov policy scan
make tf-aws-on-floci  # full aws-dev apply/re-plan/destroy against a throwaway Floci
```

Most recent run: `tf-validate` clean across all 3 environments (`local-floci`, `aws-dev`,
`aws-prod`); `tflint` clean; `checkov` **302 passed, 0 failed, 49 skipped** (every skip
carries an inline reason next to the resource it concerns, 24 distinct `#checkov:skip`
lines across the tree); `tf-aws-on-floci` completed a full apply, re-plan and destroy of
the `aws-dev` environment against a throwaway Floci, **172 resources added on apply**,
**zero adds or replacements on re-plan** (the only drift was 6 documented
Floci-non-persisted attributes: the ElastiCache replication group, the RDS instance and
its parameter group, the KMS key, a flow log, and one CloudWatch alarm statistic, not a
real plan difference), **168 resources destroyed** (ECR is left to the emulator by design,
documented in the script). `local-floci`'s own one-shot Terraform apply also runs on
every `make up`, with `storage=s3` and `key_mode=kms` genuinely exercised against it by
the running app, not just provisioned and ignored.

## Sanitization

```sh
make sanitize
```

Scans every committable file for secrets, cloud account identifiers, unresolved `.env`
values, and em or en dashes (U+2014, U+2013), which are banned everywhere in this
program's repositories. Most recent run: clean.

## Purpose check

Every acceptance criterion in [docs/PLAN.md](../PLAN.md) maps to real, independently
verified behavior:

| AC | Verified by |
| --- | --- |
| AC1 sealed secrets | `e2e/tests/08-audit-and-access.spec.ts` (no secret field on any connection response), integration tests |
| AC2 safety limits | `e2e/tests/03-read-query.spec.ts` (scan-limit refusal), integration tests |
| AC3 database-enforced read-only | integration tests per engine, `connections/connectors/openers.py`'s own design |
| AC4 AI never writes | `e2e/tests/04-ai-refused-write.spec.ts`, integration tests |
| AC5 confirmed, audited writes | `e2e/tests/05-confirmed-write-audit.spec.ts` (the full three-entry audit trail) |
| AC6 real schema discovery | `e2e/tests/06-schema-explorer.spec.ts`, integration tests |
| AC7 real, restorable backups | `e2e/tests/07-backups.spec.ts` (a real manual backup with a passing integrity check), integration tests |
| AC8 reviewed migrations | integration tests (`comparisons`) |
| AC9 connection grants, 404 not 403 | `e2e/tests/08-audit-and-access.spec.ts` |
| AC10 one audit path | `e2e/tests/05-confirmed-write-audit.spec.ts`, integration tests |
| AC11 engine parity | `e2e/tests/06-schema-explorer.spec.ts` (MongoDB's dedicated panel), integration tests per engine |
| AC12 live aggregates, real worker | integration tests (`worker`, `dashboard`, `monitoring`), `make ps` showing a real `worker` container |
| AC13 real weak-config check | `docs/assets/screenshots/04-connection-detail.png` (a live pass/warn result from the real engine), integration tests |
| AC14 SDK parity | not applicable: no SDKs shipped in 0.1.0 |

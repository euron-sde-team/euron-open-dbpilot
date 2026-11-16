# Contributing

Thanks for helping improve Euron Open DBPilot. This guide covers the workflow, the bar a
change has to clear, and the conventions the codebase follows.

## Ground rules

- **Open an issue first** for anything larger than a bug fix or a docs tweak, so we can
  agree on the approach before you invest time.
- **One concern per pull request.** A refactor and a feature are two PRs.
- **Security issues go through private reporting**, never a public issue. See
  [SECURITY.md](SECURITY.md).
- By contributing you agree your work is released under the [MIT License](LICENSE).

## Development setup

Requirements: Docker, Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 22 and
pnpm 9 (`corepack enable`).

```bash
make up          # Postgres, Redis, Floci (provisioned by Terraform), the five demo
                 # source databases, mock AI, the worker, api and web
make demo        # a seeded organization: connections, query history, backups, a comparison
make test        # unit tests (API and web)
```

[docs/guides/quickstart-local.md](docs/guides/quickstart-local.md) walks through the local
stack and your first read-only query in detail.

## Before you open a pull request

Each of these must pass:

```bash
make lint typecheck test
make test-integration                  # the API against real Postgres, Redis, the mock AI
                                        # and the five demo source databases
make test-matrix                       # when you touched storage, sealing or connector caching
make e2e                               # when you touched a dashboard flow
make tf-validate tf-lint tf-security   # when you touched infra/terraform
make tf-aws-on-floci                   # same: apply, re-plan and destroy aws-dev against Floci
make openapi                           # when you changed an endpoint (checked for drift)
make sanitize                          # no credentials, account ids, resource ids or dashes
```

## Conventions

**Package by domain.** `apps/api/src/dbpilot/<domain>/` owns its `router.py` (HTTP only, no
SQL, no business rules), `schemas.py` (the public contract), `service.py` (the business
rules) and `models.py`. Dependencies point inward: router, service, models. A domain calls
another domain's **service**, never its models directly.

**The gateway is the only door to a connected database.** Nothing outside
`connections/gateway.py` may import `connections/connectors/openers.py` directly
(`tests/unit/connections/test_one_door.py` asserts this by import graph). A new feature
that needs to read or write a connected database goes through the gateway, so it
automatically gets the read-only enforcement, the audit entry and the access check every
other query gets. If you find yourself importing a connector module directly from a new
service, that is the signal you are building a second door; route through the gateway
instead.

**The AI assistant never gets a write path.** A refusal from the SQL guard on an
AI-generated statement is final: no flag, no retry with different SQL, no approval screen.
See [ADR-0002](docs/architecture/adr/0002-ai-never-writes.md). If your feature wants the
assistant to propose a fix-up statement, the answer is still "the human types it and marks
it as a write," not a new override path.

**A confirmation needs both a token and a digest.** Any new confirmable action (a write, a
migration, a restore, or a future one) goes through `confirmations/service.py`'s existing
`create`/`begin`/`finish` flow, never a single-id "are you sure" dialog. See
[ADR-0003](docs/architecture/adr/0003-two-step-confirm-token-and-digest.md).

**A cross-project or no-grant lookup is 404, never 403.** `connections/access.py`'s
`accessible()` is the one resolver every connection-scoped route goes through. A new
connection-scoped route calls it rather than checking role or grant inline.

**The caching layers are documented, not incidental.** The schema snapshot cache, the
connection health cache and the dashboard aggregate cache are each described in
[docs/architecture/HLD.md](docs/architecture/HLD.md)'s caching table. A change to any of
them (TTL, invalidation trigger, key shape) updates that table in the same PR, and keeps
the degrade-to-a-live-read property: a cache outage must never block a query or hide data,
only make the next read slower.

**Configuration is validated.** A new setting goes in `core/config.py`, is documented in
[docs/guides/configuration.md](docs/guides/configuration.md) in the same PR, and
security-relevant ones are enforced by `Settings.guard()` at boot.

**Database changes are new migrations.** Add an Alembic revision under
`apps/api/src/dbpilot/migrations/versions/`. Never edit an applied one. Indexes for every
new filter the API exposes belong in the same revision.

**A background job is a real registered CLI subcommand.** The scheduled-backup worker is
`dbpilot worker`, wired to a real Compose service and a real Terraform ECS task
definition. A new background job follows the same pattern: add the CLI subcommand first,
wire it into Compose and Terraform in the same change, never a `command:` with nothing
behind it.

**Comments explain why.** The code says what it does. Comments are for the constraint, the
incident, or the trade-off that made it this way.

**Style.** ruff and mypy `--strict` for Python; ESLint and TypeScript strict for the web.
No em dashes or en dashes anywhere, in code, comments, docs, commit messages or PR text
(the sanitization check enforces it; use a colon, a comma, parentheses, a period, or "to"
for a range instead).

## Engine parity

PostgreSQL and MySQL support every 0.1.0 feature; SQLite and SQL Server support explorer,
SQL editor, AI assistant and backup/restore; MongoDB and Redis get their own read-only
explorer and query surface, not a forced SQL abstraction (see
[ADR-0001](docs/architecture/adr/0001-mongodb-redis-dedicated-panels.md)). A new SQL-editor
feature does not need to work for MongoDB or Redis; document the gap honestly instead of
faking support.

## Commit messages

Imperative mood with an optional scope: `connections: refuse a host that resolves to
instance metadata`, `web: show the digest on the confirm dialog`. Explain the why in the
body when it is not obvious. No em dashes or en dashes.

## Architecture decisions

Significant decisions are recorded in
[docs/architecture/adr/](docs/architecture/adr/). If your change reverses or extends one,
add a new ADR rather than editing the old one.

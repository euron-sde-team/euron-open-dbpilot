# Security Policy

DBPilot seals the credentials of every database connection it holds, and its entire
purpose is to guarantee that a query that was not deliberately marked as a write cannot
write, so we take reports seriously and respond quickly.

## Supported versions

| Version | Supported |
| --- | --- |
| 0.x (latest minor) | Yes |
| Older minors | No |

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Report privately through GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
on this repository ("Security" tab, "Report a vulnerability").

Please include:

- the affected component (`apps/api`, `apps/web`, `infra/terraform/...`) and version or
  commit,
- a description of the issue and its impact,
- steps to reproduce, or a proof of concept,
- any suggested mitigation.

What to expect:

| Stage | Target |
| --- | --- |
| Acknowledgement | within 3 business days |
| Initial assessment and severity | within 7 business days |
| Fix or mitigation for critical and high issues | within 30 days |
| Coordinated public disclosure | after a fix is released, credited to you unless you prefer otherwise |

## Scope

In scope: the code in this repository and the infrastructure it provisions with its
default settings. We are especially interested in:

- any statement that is not explicitly marked as an intentional write, and still writes
  to a connected database, whether it came from the SQL editor, the MongoDB/Redis panel,
  or the AI assistant,
- any way for an AI-generated statement to execute despite a guard refusal (no override
  path is supposed to exist anywhere in 0.1.0),
- any way to recover a connection's sealed password from the API, logs, or database
  without going through the gateway's own sealed-storage boundary,
- any way for a project member with no grant on a connection to see it, its schema, its
  query history, or run anything against it,
- any way to confirm (execute) a pending write, migration or restore without presenting
  both the exact one-time token and the exact digest `create` returned,
- any way for the audit log to omit an entry for a query execution, an AI generation, a
  confirmation step, a backup, a restore or a migration run,
- authentication or authorization bypasses in the dashboard or the API,
- a way to recover a session, the `LOCAL_MASTER_KEY`, or a KMS-sealed secret from the API,
  logs or database.

Out of scope:

- **The demo seed.** `make demo` creates well-known demo passwords on purpose, published
  in this repository's own documentation and in `deploy/compose/.env` after `make env`.
- **The mock AI service** (`apps/api/src/dbpilot/mockai`), a test and demo fixture, never
  deployed to AWS.
- **SQL Server's read-only guarantee being partly application-enforced.** The engine has
  no native read-only session flag; DBPilot uses nested, always-rolled-back transactions
  plus a nesting check, documented honestly in
  [docs/architecture/security.md](docs/architecture/security.md), and recommends a
  `db_datareader` login for a database-enforced guarantee. This is a documented design
  limit, not a vulnerability, unless you find a way to actually write through it.
- **In-database role/privilege auditing not being implemented.** 0.1.0 reports what
  DBPilot itself enforces (connections, grants, the guard), not a full audit of a
  connected database's own internal grants; this is an explicit Roadmap item (see
  [docs/PLAN.md](docs/PLAN.md)).
- **An approval workflow for AI-proposed writes not existing.** This is deliberate (see
  [ADR-0002](docs/architecture/adr/0002-ai-never-writes.md)): 0.1.0's answer to "can the AI
  write" is "no," full stop, not "yes, behind an approval screen," so there is no such
  screen to find a bypass in.
- Findings that require a compromised host, a compromised AWS account, or leaked
  deployment secrets.
- Denial of service through resource exhaustion of a deployment you operate yourself.

## What this product actually protects, so a report can be evaluated accurately

- **Credentials.** A connection's password is sealed with envelope encryption bound to the
  connection's own record id (AES-256-GCM locally, AWS KMS Encrypt/Decrypt in `key_mode=
  kms`), opened only inside the gateway at the moment a connection is used, and never
  returned by any API response, for any role, at any time.
- **Read-only is enforced twice.** The SQL guard (parses and accepts only one read-only
  statement) and the connected database's own read-only session setting, independently.
  See [docs/architecture/security.md](docs/architecture/security.md).
- **An AI-generated write has no path to execution.** A refusal here is unconditional.
- **A confirmed write requires proof of two things at once**: that the caller was shown
  the confirmation (the one-time token) and that the statement about to run is
  byte-identical to the one shown (the digest). See
  [ADR-0003](docs/architecture/adr/0003-two-step-confirm-token-and-digest.md).
- **Connection access is structural.** A connection with no grant 404s on every route,
  identical to one that does not exist.
- **The audit log cannot be bypassed by construction**: every domain that reaches the
  gateway writes through `audit/service.py`, the only code path that inserts into
  `audit_log`.

Read the full design in [docs/architecture/security.md](docs/architecture/security.md) and
[docs/architecture/threat-model.md](docs/architecture/threat-model.md) before filing a
report, so the report can focus on where the actual design differs from the actual code,
not on a misunderstanding of what 0.1.0 claims to do.

## Hardening checklist for operators

- Set a real `LOCAL_MASTER_KEY` and `SESSION_SECRET` (32+ random characters, never a
  default) before running with `ENVIRONMENT=production`; the boot guard refuses to start
  otherwise. See [docs/guides/configuration.md](docs/guides/configuration.md).
- Set `COOKIE_SECURE=true` and a real `CORS_ORIGINS` allowlist (never `*`) in production;
  also enforced by the boot guard.
- Prefer `KEY_MODE=kms` and `STORAGE=s3` in production; `infra/terraform` provisions both
  for `aws-dev`/`aws-prod`, and the boot guard refuses `STORAGE=local` in production.
- Grant connection access, and especially the ability to mark writes, only to the people
  who genuinely need it; review `connection_grants` periodically.
- Prefer a `db_datareader` (or equivalent read-only) login for SQL Server connections, for
  a database-enforced read-only guarantee rather than DBPilot's application-level check
  alone.
- Terminate TLS in front of the deployment; DBPilot's own app layer adds no transport
  encryption of its own.

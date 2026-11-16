# Troubleshooting

## "An AI-generated query was refused and there's no way to run it anyway"

This is the product working as designed, not a bug (acceptance criterion AC4). An
AI-generated statement that the guard identifies as anything other than exactly one
read-only query is refused unconditionally: there is no flag, no approval and no "run
anyway" button anywhere in 0.1.0. See
[ADR-0002](../architecture/adr/0002-ai-never-writes.md). If you genuinely need that
statement to run, type it yourself in the editor and mark it as an intentional write,
which goes through the human confirmation path instead (AC5).

## "A query I know is read-only was refused by the guard"

The guard (`queries/guard.py`) accepts only a single statement it can parse as exactly one
read-only query in the connection's dialect. A multi-statement string, a statement using
syntax the parser does not recognize for that dialect, or a statement whose shape it
cannot classify is refused rather than guessed at. Check the guard's `reason` in the
response; if the statement really is a single `SELECT`, check for a trailing semicolon
plus trailing content, or dialect-specific syntax (`sqlglot`'s dialect for that engine) the
parser does not yet support, and simplify or rewrite.

## "A confirmed write says `digest_mismatch` or `token mismatch`"

The confirmation's token and digest must both match exactly what `create` returned, and
the confirmation must still be pending and unexpired (default 600 seconds,
`CONFIRMATION_TTL_S`). This is deliberate (acceptance criterion AC5,
[ADR-0003](../architecture/adr/0003-two-step-confirm-token-and-digest.md)): a confirmation
id alone is never enough to execute anything. If the window expired, start the
confirmation over; if the digest does not match, the statement you are trying to run is
not byte-identical to the one you were shown, which the confirmation refuses on purpose.

## "A connection fails with `InvalidCiphertextException` when calling Decrypt"

This means the connection's `secret_sealed` was encrypted under a KMS key that no longer
exists: Floci's KMS state is tied to its own data volume, and an earlier `make down`
(which removes `dbpilot_pgdata` but is not guaranteed to also remove Floci's own KMS state
the exact same way across every Docker/Floci version) or a Floci container recreated
without its volume can leave Postgres holding ciphertext sealed under a previous
`alias/dbpilot-local` key id while a fresh key has since been created under the same
alias. This is observable directly in `make logs SERVICE=terraform` and
`make logs SERVICE=floci`: a `Created KMS alias: alias/dbpilot-local -> <new key id>` line
after the stack already had seeded connections means every previously sealed secret is now
undecryptable. There is no in-place re-seal in 0.1.0 (see
[operations.md](operations.md)'s "Rotating secrets" section, which has the identical
limitation for a deliberate rotation); the fix is the same either way: `make down && make
up && make demo` for a fully fresh cycle, so the KMS key and every sealed secret are
created together again. This is a genuine Floci-persistence gap, not an application bug:
the same ciphertext sealed under a real, unrotated AWS KMS key on `aws-dev`/`aws-prod`
does not have this problem.

## "The API fails to boot with `KEY_MODE=kms` or `STORAGE=s3` locally"

Both are fully implemented in 0.1.0, unlike a stub backend. Check `AWS_ENDPOINT_URL` points
at Floci (`http://floci:4566` inside Compose, `http://localhost:5866` from the host) and
that the `local-floci` Terraform has actually applied (`make logs SERVICE=terraform`); a
KMS or S3 call with no reachable endpoint fails at the AWS SDK level with a connection
error, not a DBPilot-specific one.

## "The connection health card shows a weak-configuration warning"

This is a real, live check against the connected engine's own setting (Postgres's `ssl`
GUC and `pg_stat_ssl`, MySQL's `require_secure_transport`, similar for MSSQL/Mongo/Redis),
never a hardcoded placeholder (acceptance criterion AC13). If you believe the warning is
wrong, check the engine's actual setting directly (for Postgres, `SHOW ssl;`), not just
DBPilot's connection string, since `ssl_mode` in the saved connection config is what
DBPilot asked for, while the warning reports what the server actually negotiated.

## "I have no grant on a connection and it returns 404, not 403"

By design (acceptance criterion AC9): a connection a caller has no grant on answers 404 on
every route, identical to one that does not exist, so an unauthorized caller cannot even
confirm the connection exists. Ask a project admin or owner for a grant
(`POST /api/v1/connections/{id}/grants`).

## "The API fails to boot in production with a config error"

`Settings.guard()` only runs when `ENVIRONMENT=production`, and lists every problem it
finds in one `RuntimeError`: an under-length or default `SESSION_SECRET`/`LOCAL_MASTER_KEY`,
`STORAGE=local`, `AI_PROVIDER=mock`, a `CORS_ORIGINS` containing `*`, `COOKIE_SECURE` not
`true`, or `PUBLIC_URL` not `https://`. Read the full message, it names every field that
needs fixing at once. See [configuration.md](configuration.md)'s "Production boot guard"
section.

## "`make up` fails with a port already in use"

Another process on your machine is already bound to one of DBPilot's local ports (web
5810, api 5830, postgres 5842, redis 5879, floci 5866, mailpit 5825, mock AI 5850, demo
source postgres 5843, mysql 5806, mssql 5807, mongo 5808, redis 5809). Set the matching
`DBPILOT_*_PORT` variable in `deploy/compose/.env` to a free port, or stop the other
process. Never 3000, 3001, 5060, 4040 or 8081, reserved by other work on this program.

## "`make down` deleted my data"

By design: `make down` removes the stack's local Docker volumes (Postgres, demo source
databases, object storage, Floci state) along with the containers.

## "Puppeteer QA (`make qa`) or Playwright e2e (`make e2e`) can't sign in"

Both need the demo organization seeded first (`make demo`); signing in before that fails
because the demo users do not exist yet. They read the same `DBPILOT_DEMO_PASSWORD` from
`deploy/compose/.env` that `make demo` seeded with; if you regenerated `.env` after
seeding, the password in the file no longer matches the one the seeded users actually
have, re-run `make demo` (it is idempotent) or re-seed against a fresh `make down && make
up`.

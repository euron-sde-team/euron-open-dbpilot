# Quickstart (local)

This gets you a running DBPilot stack, a seeded demo organization, and your first
read-only query against a real connected database, with no AWS account and no host
dependency beyond Docker.

## Prerequisites

- Docker and Docker Compose.
- `curl` (or any HTTP client) to try the API directly.
- Nothing else. Postgres, Redis, Floci (the AWS emulator), the five demo source
  databases, the mock AI service, the API, the worker and the dashboard all run as
  Compose services.

## 1. Generate local secrets

```sh
make env
```

Writes `deploy/compose/.env` (gitignored) with random values for `SESSION_SECRET` and
`LOCAL_MASTER_KEY`, the demo organization's password, the demo source databases' admin
password, the demo read-only login's password, and SQL Server's `sa` password. Safe to
re-run; it will not overwrite an existing file your running stack already depends on.

## 2. Start the stack

```sh
make up
```

Brings up Postgres, Redis, Floci (with a one-shot Terraform apply against it), the five
demo source databases (PostgreSQL, MySQL, SQL Server, MongoDB, Redis), the mock AI
service, the scheduled-backup worker, the API and the web dashboard, waiting until every
service reports healthy. On success you will see:

```
  dashboard     http://localhost:5810
  api docs      http://localhost:5830/docs
  mail          http://localhost:5825   (Mailpit)
  mock AI       http://localhost:5850
```

| Service | Local port |
| --- | --- |
| web (dashboard) | 5810 |
| api | 5830 |
| postgres (DBPilot's own) | 5842 |
| redis | 5879 |
| floci (AWS emulator) | 5866 |
| mailpit | 5825 |
| mock AI | 5850 |
| demo source: postgres (shop) | 5843 |
| demo source: mysql (people) | 5806 |
| demo source: sql server (inventory) | 5807 |
| demo source: mongodb (catalog) | 5808 |
| demo source: redis (sessions) | 5809 |

## 3. Seed the demo organization

```sh
make demo
```

Runs `dbpilot seed` inside the `api` container: a realistic organization with connections
to all five demo source databases, query history, a few backups and a schema comparison,
so the dashboard looks alive from the first sign-in. It prints the seeded logins:

```
  maya.chen@lumenlogistics.example           project owner
  arjun.rao@lumenlogistics.example           project admin
  sofia.alvarez@lumenlogistics.example       project viewer
  liam.okafor@lumenlogistics.example         project viewer
  password: the DBPILOT_DEMO_PASSWORD value from deploy/compose/.env
```

Sign in at [http://localhost:5810](http://localhost:5810).

## 4. Run your first read-only query

From the dashboard: open the **Lumen Logistics** project, open the **shop** connection
(the demo PostgreSQL source), and open the SQL editor. Try:

```sql
select customer_id, count(*) as orders from orders group by customer_id order by orders desc limit 10;
```

Run it. The result is paginated and exportable as CSV. Try a write next
(`update orders set status = 'cancelled' where id = 1;`) and mark it as an intentional
write: you will see the two-step confirmation (the exact statement, a token and a digest)
before anything reaches the database, and a new audit entry once it runs.

Or from the API directly, once signed in (the dashboard sends a session cookie; for a
script, use the same `/api/v1/auth/login` route and reuse the cookie):

```sh
curl -s http://localhost:5830/api/v1/connections -b cookies.txt
```

## 5. Ask the AI assistant

Open the SQL editor's AI panel and ask something like "which customers have not ordered
in the last 30 days". The assistant generates one read-only statement against the real
discovered schema and shows it before running; try asking for something that implies a
write ("delete inactive customers") and watch it refuse, with no way to override, which is
the whole point (see [docs/architecture/security.md](../architecture/security.md)).

## Next steps

- [configuration.md](configuration.md): every environment variable and what it controls.
- [walkthrough.md](walkthrough.md): a full tour of the dashboard.
- [testing.md](testing.md): running the test suites yourself.
- `make down` stops the stack and deletes its local data (Postgres, demo source, Floci
  volumes).

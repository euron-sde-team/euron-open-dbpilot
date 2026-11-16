"""The read path: AC2 (safety limits) and AC3 (read-only enforced by the database, not only
by the parser), on real PostgreSQL, MySQL and SQLite."""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest

from dbpilot.connections.connectors.openers import open_sql
from dbpilot.connections.connectors.sql import estimate_scan, run_query
from dbpilot.connections.connectors.targets import SqlTarget
from tests.integration.conftest import (
    HOST,
    PG_PORT,
    SOURCE_PASSWORD,
    Sources,
    Stack,
    source_engine,
    source_scalar,
)

pytestmark = pytest.mark.integration


async def test_ac2_a_query_above_the_scan_limit_is_refused_before_it_runs(
    ready: Stack, sources: Sources
) -> None:
    conn = await ready.connection(sources.pg(max_scan_rows=1000, timeout_s=5))
    # A triple cross join of 2,500 orders would read 15.6 billion rows and time out after
    # 5 s if it ran; the planner's estimate stops it first.
    out = await ready.sql(conn["id"], "SELECT count(*) FROM orders a, orders b, orders c")
    assert out["status"] == "scan_limit" and out["error_code"] == "scan_limit"
    # The inner sides of the nested loops run once per outer row: the estimate is the work.
    assert out["estimated_scan_rows"] >= 2500**3 and out["row_count"] == 0
    assert out["duration_ms"] == 0 and out["page"] is None
    assert "more than this connection allows (1,000)" in out["error"]
    entry = (await ready.audit(action="query.run", connection_id=conn["id"]))[0]
    assert entry["details"]["status"] == "scan_limit"
    small = await ready.sql(conn["id"], "SELECT * FROM products WHERE id = 3")
    assert small["status"] == "succeeded" and small["estimated_scan_rows"] is not None


async def test_ac2_a_result_above_max_rows_is_cut_and_says_so(
    ready: Stack, sources: Sources
) -> None:
    for body in (sources.pg(max_rows=10), sources.mysql(max_rows=10)):
        conn = await ready.connection(body)
        table = "customers" if body["kind"] == "postgresql" else "employees"
        out = await ready.sql(conn["id"], f"SELECT * FROM {table} ORDER BY id")
        assert out["status"] == "succeeded"
        assert out["row_count"] == 10 and out["truncated"] is True
        assert out["page"]["total"] == 10
        exact = await ready.sql(conn["id"], f"SELECT * FROM {table} ORDER BY id LIMIT 10")
        assert exact["row_count"] == 10 and exact["truncated"] is False


async def test_results_page_and_export_without_querying_again(
    ready: Stack, sources: Sources
) -> None:
    conn = await ready.connection(sources.pg())
    out = await ready.ok(
        "POST",
        ready.p("/queries/sql"),
        {
            "connection_id": conn["id"],
            "sql": "SELECT id, name FROM customers ORDER BY id LIMIT 30",
            "page_size": 5,
        },
    )
    assert out["page"]["rows"][0][0] == 1 and len(out["page"]["rows"]) == 5
    page = await ready.ok(
        "GET", ready.p(f"/queries/{out['id']}/rows"), params={"offset": 25, "limit": 10}
    )
    assert [r[0] for r in page["rows"]] == [26, 27, 28, 29, 30] and page["total"] == 30
    csv = await ready.client.get(ready.p(f"/queries/{out['id']}/export"), params={"format": "csv"})
    assert csv.status_code == 200 and csv.text.splitlines()[0] == "id,name"
    assert len(csv.text.strip().splitlines()) == 31
    js = await ready.client.get(ready.p(f"/queries/{out['id']}/export"), params={"format": "json"})
    assert js.json()[0]["id"] == 1
    history = await ready.ok("GET", ready.p("/queries"), params={"connection_id": conn["id"]})
    assert history["items"][0]["id"] == out["id"]


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM orders",
        "UPDATE orders SET total_amount = 0",
        "INSERT INTO notes (body) VALUES ('x')",
        "DROP TABLE notes",
        "TRUNCATE notes",
        "SELECT 1; DELETE FROM orders",
        "SELECT pg_sleep(1)",
        "WITH gone AS (DELETE FROM orders RETURNING *) SELECT count(*) FROM gone",
        "SELECT * INTO stolen FROM orders",
        "SELECT * FROM orders FOR UPDATE",
        "COPY orders TO '/tmp/orders.csv'",
    ],
)
async def test_ac3_the_guard_refuses_before_the_database_is_reached(
    ready: Stack, sources: Sources, sql: str
) -> None:
    conn = await ready.connection(sources.pg(admin=True))  # this login could write
    before = source_scalar("postgresql", "shop_test", "SELECT count(*) FROM orders")
    out = await ready.sql(conn["id"], sql)
    assert out["status"] == "refused", out
    assert out["row_count"] == 0 and out["duration_ms"] == 0
    assert source_scalar("postgresql", "shop_test", "SELECT count(*) FROM orders") == before
    entry = (await ready.audit(action="query.refused", connection_id=conn["id"]))[0]
    assert entry["details"]["statement"] == sql.strip()
    # Refused before any database contact: no connector call was audited for it.
    assert not await ready.audit(action="query.run", connection_id=conn["id"])


async def test_ac3_a_statement_the_parser_accepts_still_cannot_write_on_postgres(
    ready: Stack, sources: Sources
) -> None:
    """``SELECT dbp_evil()`` is a plain SELECT to the parser; the function inserts a row.
    The session is read-only in Postgres itself, so the insert fails."""
    engine = source_engine("postgresql", "shop_test")
    with engine.begin() as c:
        c.exec_driver_sql(
            "CREATE OR REPLACE FUNCTION dbp_evil() RETURNS int LANGUAGE sql AS "
            "$$ INSERT INTO notes (body) VALUES ('escaped the guard') RETURNING 1 $$"
        )
    engine.dispose()
    conn = await ready.connection(sources.pg(admin=True))
    before = source_scalar("postgresql", "shop_test", "SELECT count(*) FROM notes")
    validation = await ready.ok(
        "POST",
        ready.p("/queries/validate"),
        {"connection_id": conn["id"], "sql": "SELECT dbp_evil()"},
    )
    assert validation["valid"] is True  # the parser is fooled
    out = await ready.sql(conn["id"], "SELECT dbp_evil()")
    assert out["status"] == "failed"
    assert "read-only transaction" in out["error"]
    assert source_scalar("postgresql", "shop_test", "SELECT count(*) FROM notes") == before


async def test_ac3_a_statement_the_parser_accepts_still_cannot_write_on_mysql(
    ready: Stack, sources: Sources
) -> None:
    engine = source_engine("mysql", "people_test")
    with engine.begin() as c:
        c.exec_driver_sql("DROP FUNCTION IF EXISTS dbp_evil")
        c.exec_driver_sql(
            "CREATE FUNCTION dbp_evil() RETURNS INT DETERMINISTIC MODIFIES SQL DATA "
            "BEGIN INSERT INTO departments (name, location) VALUES ('Evil', 'Nowhere'); RETURN 1; END"
        )
    engine.dispose()
    conn = await ready.connection(sources.mysql(admin=True))
    before = source_scalar("mysql", "people_test", "SELECT count(*) FROM departments")
    out = await ready.sql(conn["id"], "SELECT dbp_evil()")
    assert out["status"] == "failed", out
    assert "READ ONLY" in out["error"] or "read only" in out["error"].lower()
    assert source_scalar("mysql", "people_test", "SELECT count(*) FROM departments") == before


def _pg_admin_target() -> SqlTarget:
    return SqlTarget(
        kind="postgresql",
        host=HOST,
        port=PG_PORT,
        database="shop_test",
        username="shop_admin",
        password=SOURCE_PASSWORD,
        ssl_mode="disable",
    )


@pytest.mark.parametrize(
    "crafted",
    [
        "INSERT INTO notes (body) VALUES ('x')",
        "COMMIT; BEGIN READ WRITE; INSERT INTO notes (body) VALUES ('escaped'); COMMIT",
        "SELECT 1; INSERT INTO notes (body) VALUES ('x')",
        "SET TRANSACTION READ WRITE",
        "CREATE TABLE dbp_evil_table (a int)",
    ],
)
def test_ac3_crafted_statements_sent_straight_to_the_connector_cannot_write(crafted: str) -> None:
    """Bypassing the guard entirely: the read-only session (and the extended protocol that
    allows one statement) stops every one of these in Postgres itself. Over the simple
    protocol the second one would escape: DBPilot never uses it on a read session."""
    before = source_scalar("postgresql", "shop_test", "SELECT count(*) FROM notes")
    for runner in ("run_query", "estimate_scan"):
        with open_sql(_pg_admin_target(), 10, mode="read") as c:
            if runner == "run_query":
                with pytest.raises(Exception):  # noqa: B017 any refusal is the point
                    run_query(c, crafted, 10)
            else:
                # EXPLAIN only plans (an INSERT plans fine); a smuggled second statement
                # is refused by the server. Either way nothing may be written.
                with contextlib.suppress(Exception):
                    estimate_scan(c, crafted, [], {})
    assert source_scalar("postgresql", "shop_test", "SELECT count(*) FROM notes") == before
    assert not source_scalar(
        "postgresql",
        "shop_test",
        "SELECT count(*) FROM pg_tables WHERE tablename = 'dbp_evil_table'",
    )


def test_ac3_sqlite_is_read_only_in_sqlite_itself(sources: Sources, tmp_path: Path) -> None:
    import shutil
    import sqlite3

    copy = tmp_path / "copy.sqlite"
    shutil.copyfile(sources.sqlite_path, copy)
    before = copy.read_bytes()
    target = SqlTarget(kind="sqlite", sqlite_path=str(copy))
    for crafted in (
        "INSERT INTO plans VALUES (9, 'x', 1)",
        "DELETE FROM invoices",
        "CREATE TABLE evil (a int)",
        f"ATTACH DATABASE '{tmp_path / 'evil.db'}' AS evil",
        "PRAGMA query_only = 0",
    ):
        with open_sql(target, 10, mode="read") as c, pytest.raises(Exception):  # noqa: B017
            run_query(c, crafted, 10)
    assert copy.read_bytes() == before
    assert not (tmp_path / "evil.db").exists()
    with sqlite3.connect(copy) as db:
        assert db.execute("SELECT count(*) FROM plans").fetchone()[0] == 4


async def test_the_session_reports_read_only_on_every_server_engine(
    ready: Stack, sources: Sources
) -> None:
    for body in (sources.pg(admin=True), sources.mysql(admin=True)):
        conn = await ready.connection(body)
        out = await ready.ok("POST", ready.p(f"/connections/{conn['id']}/test"))
        assert out["read_only"] is True, out


async def test_validate_and_format_never_run_anything(ready: Stack, sources: Sources) -> None:
    conn = await ready.connection(sources.pg())
    bad = await ready.ok(
        "POST",
        ready.p("/queries/validate"),
        {"connection_id": conn["id"], "sql": "DROP TABLE orders"},
    )
    assert bad["valid"] is False and bad["code"] == "not_a_select"
    good = await ready.ok(
        "POST", ready.p("/queries/validate"), {"dialect": "mysql", "sql": "select 1"}
    )
    assert good["valid"] is True
    fmt = await ready.ok(
        "POST",
        ready.p("/queries/format"),
        {"dialect": "postgresql", "sql": "select a,b from t where a=1"},
    )
    assert fmt["sql"].startswith("SELECT\n")
    assert not await ready.audit(action="query.", connection_id=conn["id"])


async def test_ac2_a_table_outside_public_is_counted_whole(ready: Stack, sources: Sources) -> None:
    engine = source_engine("postgresql", "shop_test")
    with engine.begin() as c:
        c.exec_driver_sql("DROP SCHEMA IF EXISTS ledger CASCADE")
        c.exec_driver_sql("CREATE SCHEMA ledger")
        c.exec_driver_sql(
            "CREATE TABLE ledger.entries AS SELECT g AS id, 'n' || g AS note "
            "FROM generate_series(1, 20000) g"
        )
        c.exec_driver_sql("ANALYZE ledger.entries")
        c.exec_driver_sql("GRANT USAGE ON SCHEMA ledger TO dbpilot_reader")
        c.exec_driver_sql("GRANT SELECT ON ledger.entries TO dbpilot_reader")
    try:
        conn = await ready.connection(sources.pg(max_scan_rows=5000))
        # The filter keeps about one row, but the scan reads all 20,000 of them.
        out = await ready.sql(conn["id"], "SELECT * FROM ledger.entries WHERE note = 'x'")
        assert out["status"] == "scan_limit" and out["estimated_scan_rows"] >= 20000, out
    finally:
        with engine.begin() as c:
            c.exec_driver_sql("DROP SCHEMA ledger CASCADE")
        engine.dispose()


async def test_ac2_mysql_estimates_a_join_as_the_rows_it_combines(
    ready: Stack, sources: Sources
) -> None:
    conn = await ready.connection(sources.mysql(max_scan_rows=10_000))
    # 200 employees: summed per table that is 600 rows, but the join combines 8 million.
    out = await ready.sql(conn["id"], "SELECT count(*) FROM employees a, employees b, employees c")
    assert out["status"] == "scan_limit" and out["estimated_scan_rows"] >= 200**3, out
    joined = await ready.sql(
        conn["id"],
        "SELECT d.name, count(*) AS n FROM employees e JOIN departments d ON d.id = e.department_id "
        "GROUP BY d.name ORDER BY n DESC",
    )
    assert joined["status"] == "succeeded" and joined["row_count"] > 0, joined


async def test_ac3_mysql_refuses_executable_comments_and_runs_what_the_guard_read(
    ready: Stack, sources: Sources
) -> None:
    conn = await ready.connection(sources.mysql(admin=True))
    for sql in (
        "SELECT 1 /*!50000 , LOAD_FILE('/etc/passwd') */",
        "SELECT * FROM employees /*!50000 INTO OUTFILE '/var/lib/mysql-files/x' */",
        "SELECT /*+ MAX_EXECUTION_TIME(999999999) */ * FROM employees",
    ):
        out = await ready.sql(conn["id"], sql)
        assert out["status"] == "refused" and out["error_code"] == "hidden_code", out
    # Ordinary comments are dropped from what is sent; the statement itself still runs.
    out = await ready.sql(
        conn["id"],
        "SELECT DATE_FORMAT(hire_date, '%Y') AS y, count(*) AS n -- per year\n"
        "FROM employees GROUP BY y ORDER BY y /* oldest first */",
    )
    assert out["status"] == "succeeded" and out["row_count"] > 0, out
    assert out["statement"].endswith("/* oldest first */")  # history keeps what was typed

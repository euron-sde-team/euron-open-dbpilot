"""The SQL guard: the read-only gate (AI and editor) and the write classifier (humans)."""

from __future__ import annotations

import pytest

from dbpilot.queries import guard

READS = [
    ("SELECT 1", "postgresql"),
    ("select * from orders where note = 'a;b'", "mysql"),
    ("WITH t AS (SELECT 1 AS a) SELECT a FROM t", "postgresql"),
    ("SELECT a FROM x UNION SELECT a FROM y", "sqlite"),
    ("SELECT TOP 5 name FROM dbo.customers ORDER BY name", "mssql"),
    ("SELECT count(*) OVER (PARTITION BY a) FROM t", "postgresql"),
    ("SELECT 1;", "mysql"),
]
REFUSED = [
    ("DELETE FROM orders", "postgresql", "not_a_select"),
    ("UPDATE t SET a = 1", "mysql", "not_a_select"),
    ("INSERT INTO t VALUES (1)", "sqlite", "not_a_select"),
    ("DROP TABLE t", "mssql", "not_a_select"),
    ("SELECT 1; DELETE FROM t", "postgresql", "multiple_statements"),
    ("WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x", "postgresql", "write_or_admin"),
    ("SELECT * INTO y FROM x", "postgresql", "write_or_admin"),
    ("SELECT * FROM t FOR UPDATE", "postgresql", "write_or_admin"),
    ("SELECT pg_sleep(5)", "postgresql", "denied_function"),
    ("SELECT sleep(5)", "mysql", "denied_function"),
    ("SELECT load_extension('x')", "sqlite", "denied_function"),
    ("SELECT * FROM OPENROWSET('SQLNCLI', 'x', 'select 1')", "mssql", "table_function"),
    ("SELECT * FROM read_csv('/etc/passwd')", "postgresql", "table_function"),
    ("", "postgresql", "empty"),
    ("SELEC 1", "postgresql", "parse_error"),
    ("hello", "postgresql", "parse_error"),
    ("SELECT 1", "oracle", "unsupported_dialect"),
]


@pytest.mark.parametrize(("sql", "dialect"), READS)
def test_read_only_queries_pass(sql: str, dialect: str) -> None:
    verdict = guard.check(sql, dialect)
    assert verdict.sql == sql.strip().rstrip(";").strip()
    assert guard.is_read_only(sql, dialect) == (True, None, None)


@pytest.mark.parametrize(("sql", "dialect", "code"), REFUSED)
def test_everything_else_is_refused(sql: str, dialect: str, code: str) -> None:
    with pytest.raises(guard.UnsafeSqlError) as err:
        guard.check(sql, dialect)
    assert err.value.code == code
    ok, reason, got = guard.is_read_only(sql, dialect)
    assert ok is False and got == code and reason


def test_long_input_is_refused() -> None:
    with pytest.raises(guard.UnsafeSqlError) as err:
        guard.check("SELECT " + "1," * 60_000 + "1", "postgresql")
    assert err.value.code == "too_long"


def test_verdict_reports_tables_aliases_and_the_canonical_form() -> None:
    v = guard.check(
        "WITH c AS (SELECT * FROM sales.customers) SELECT o.id FROM orders o JOIN c ON c.id = o.customer_id",
        "postgresql",
    )
    assert set(v.tables) == {"sales.customers", "orders"}
    assert v.aliases == {"o": "orders"}
    tsql = guard.check("SELECT TOP 5 c.name FROM dbo.customers c", "mssql")
    assert tsql.canonical() == "SELECT TOP 5 c.name FROM dbo.customers AS c"


def test_split_respects_strings_and_comments() -> None:
    parts = guard.split_statements(
        "UPDATE t SET a = 'x;y' WHERE id = 1; -- c;\nDELETE FROM t;", "postgresql"
    )
    assert parts == ["UPDATE t SET a = 'x;y' WHERE id = 1", "-- c;\nDELETE FROM t"]


def test_classify_describes_each_statement() -> None:
    infos = guard.classify(
        "INSERT INTO t VALUES (1); UPDATE t SET a = 2 WHERE id = 1; DELETE FROM t; "
        "CREATE INDEX ix_t ON t (a); DROP TABLE old; ALTER TABLE t ADD COLUMN b int; "
        "ALTER TABLE t DROP COLUMN c; TRUNCATE t; SELECT 1",
        "postgresql",
    )
    assert [i.kind for i in infos] == [
        "insert",
        "update",
        "delete",
        "create",
        "drop",
        "alter",
        "alter",
        "truncate",
        "select",
    ]
    assert [i.destructive for i in infos] == [
        False,
        False,
        True,
        False,
        True,
        False,
        True,
        True,
        False,
    ]
    assert infos[2].summary == "Deletes EVERY row from t (no WHERE clause)"
    assert infos[1].has_where is True
    assert infos[3].summary == "Creates the index ix_t on t"


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("BEGIN; DELETE FROM t; COMMIT", "transaction_control"),
        ("ROLLBACK", "transaction_control"),
        ("SET search_path = x", "session_control"),
        ("USE other", "session_control"),
        ("GRANT SELECT ON t TO bob", "privileges"),
        ("VACUUM t", "unparsed_statement"),
        ("COPY t TO '/tmp/x'", "file_access"),
        ("SELECT * INTO t2 FROM t", "file_access"),
        ("SELECT pg_read_file('/etc/passwd')", "denied_function"),
        ("   ", "empty"),
    ],
)
def test_classify_refuses_what_it_cannot_describe(sql: str, code: str) -> None:
    with pytest.raises(guard.UnsafeSqlError) as err:
        guard.classify(sql, "postgresql")
    assert err.value.code == code


def test_classify_limits_and_dialects() -> None:
    with pytest.raises(guard.UnsafeSqlError) as err:
        guard.classify("SELECT 1;" * 201, "postgresql")
    assert err.value.code == "too_many_statements"
    assert [i.kind for i in guard.classify("INSERT INTO t (a) VALUES (1)", "mssql")] == ["insert"]
    with pytest.raises(guard.UnsafeSqlError):
        guard.split_statements("SELECT 1", "oracle")


def test_fingerprint_ignores_whitespace_case_and_comments() -> None:
    a = guard.fingerprint("DELETE FROM orders WHERE status = 'x'", "postgresql")
    b = guard.fingerprint("  delete   from\norders where status = 'x' -- note", "postgresql")
    assert a == b and a is not None
    assert guard.fingerprint("DELETE FROM other", "postgresql") != a
    assert guard.fingerprint("SELECT (", "postgresql") is None


def test_pretty() -> None:
    assert (
        guard.pretty("select a from t where b=1", "postgresql")
        == "SELECT\n  a\nFROM t\nWHERE\n  b = 1"
    )
    assert guard.pretty("SELEC (", "postgresql") == "SELEC ("
    assert guard.pretty("select 1; select 2", "mysql").count("SELECT") == 2

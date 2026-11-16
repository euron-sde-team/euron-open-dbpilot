"""What the guard cannot see never reaches a database: executable comments and optimizer
hints are refused, MySQL and SQL Server run the guard's own re-serialization, nesting that
would exhaust the parser is refused as a refusal (not a crash), and a statement lifted out
of a longer AI answer is still recognized."""

from __future__ import annotations

import pytest

from dbpilot.queries import guard


@pytest.mark.parametrize(
    ("sql", "dialect"),
    [
        ("SELECT 1 /*!50000 , LOAD_FILE('/etc/passwd') */", "mysql"),
        ("SELECT * FROM t /*!50000 INTO OUTFILE '/var/lib/mysql-files/x' */", "mysql"),
        ("SELECT /*+ MAX_EXECUTION_TIME(999999999) */ * FROM t", "mysql"),
        ("SELECT /*+ SET_VAR(sql_select_limit=1000000) */ * FROM t", "mysql"),
        ("SELECT 1 /*M!100000 , LOAD_FILE('/etc/passwd') */", "mysql"),
        ("SELECT a --x /*! , b */ FROM t", "mysql"),
        ("SELECT /*+ Set(statement_timeout 0) */ * FROM t", "postgresql"),
    ],
)
def test_executable_comments_and_hints_are_refused(sql: str, dialect: str) -> None:
    with pytest.raises(guard.UnsafeSqlError) as err:
        guard.check(sql, dialect)
    assert err.value.code == "hidden_code"
    with pytest.raises(guard.UnsafeSqlError) as on_write:
        guard.classify(sql.replace("SELECT", "DELETE FROM t WHERE 1 = (SELECT", 1) + ")", dialect)
    assert on_write.value.code == "hidden_code"


def test_ordinary_comments_and_lookalike_strings_pass() -> None:
    v = guard.check("SELECT '/*!50000 x */' AS s -- a note\n/* another */", "mysql")
    assert v.executable() == "SELECT '/*!50000 x */' AS s"
    assert guard.check("SELECT 1 # hash comment", "mysql").executable() == "SELECT 1"


def test_mysql_and_sql_server_run_the_canonical_form_others_the_text() -> None:
    mysql = guard.check("select  a  from t -- trailing\n", "mysql")
    assert mysql.executable() == "SELECT a FROM t" == mysql.canonical()
    pg = guard.check("select  a  from t -- trailing\n", "postgresql")
    assert pg.executable() == "select  a  from t -- trailing"
    assert guard.check("SELECT 1", "sqlite").executable() == "SELECT 1"


def test_sql_server_writes_cannot_smuggle_a_second_statement() -> None:
    # T-SQL needs no ';' between statements: SQL Server would read SHUTDOWN and RECONFIGURE
    # as statements of their own. What runs is the classifier's re-serialization, in which
    # they can only be (invalid) aliases.
    infos = guard.classify("INSERT INTO t SELECT 1 SHUTDOWN", "mssql")
    assert infos[0].sql == "INSERT INTO t SELECT 1 AS SHUTDOWN" and infos[0].kind == "insert"
    deleted = guard.classify("DELETE FROM t RECONFIGURE", "mssql")[0]
    assert deleted.sql == "DELETE FROM t AS RECONFIGURE" and deleted.destructive
    # PostgreSQL keeps the text as typed (the extended protocol refuses a second statement).
    assert guard.classify("DELETE FROM t WHERE id = 1 -- why", "postgresql")[0].sql.endswith(
        "-- why"
    )


@pytest.mark.parametrize("hint", ["TABLOCKX", "UPDLOCK, HOLDLOCK", "XLOCK", "SERIALIZABLE"])
def test_sql_server_locking_table_hints_are_refused_on_reads(hint: str) -> None:
    with pytest.raises(guard.UnsafeSqlError) as err:
        guard.check(f"SELECT * FROM dbo.t WITH ({hint})", "mssql")
    assert err.value.code == "write_or_admin"
    assert guard.check("SELECT * FROM dbo.t WITH (NOLOCK)", "mssql").tables == ["dbo.t"]


def test_deep_nesting_is_a_refusal_not_a_crash() -> None:
    deep = "SELECT " + "(" * 500 + "1" + ")" * 500
    with pytest.raises(guard.UnsafeSqlError) as err:
        guard.check(deep, "postgresql")
    assert err.value.code == "too_deep"
    with pytest.raises(guard.UnsafeSqlError) as on_write:
        guard.classify("UPDATE t SET a = " + "(" * 500 + "1" + ")" * 500, "mssql")
    assert on_write.value.code == "too_deep"
    assert guard.fingerprint(deep, "postgresql") is None
    assert guard.pretty(deep, "postgresql") == deep
    nested = "SELECT " + "(" * 30 + "1" + ")" * 30  # deep but reasonable
    assert guard.check(nested, "postgresql").canonical()
    # Parenthesis depth is the guard's own limit; a parser failure past it is still caught.
    with pytest.raises(guard.UnsafeSqlError) as parsed:
        guard.parse("SELECT " + "(" * 500 + "1" + ")" * 500, "postgresql")
    assert parsed.value.code == "too_deep"


def test_each_statement_of_a_script_has_its_own_fingerprint() -> None:
    script = "UPDATE accounts SET balance = 0 WHERE id = 7; SELECT * FROM accounts"
    prints = guard.statement_fingerprints(script, "postgresql")
    lifted = guard.fingerprint("update accounts set balance=0 where id = 7", "postgresql")
    assert lifted in prints and guard.fingerprint(script, "postgresql") in prints
    assert len(prints) == 3
    # A piece that does not parse is skipped, the rest still count.
    partial = guard.statement_fingerprints("DELETE FROM a; SELEC garbage", "postgresql")
    assert guard.fingerprint("DELETE FROM a", "postgresql") in partial
    assert guard.statement_fingerprints("", "postgresql") == []

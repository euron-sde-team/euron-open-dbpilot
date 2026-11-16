"""The SQL connector on SQLite files (no server needed): read-only sessions, discovery,
scan estimates, the dump format and its restore, values, health."""

from __future__ import annotations

import datetime as dt
import decimal
import gzip
import json
import sqlite3
import uuid
from pathlib import Path

import pytest

from dbpilot.connections.connectors.discovery import discover
from dbpilot.connections.connectors.dump import dump, restore, sha256_file, verify
from dbpilot.connections.connectors.health_sql import sql_health
from dbpilot.connections.connectors.openers import open_sql
from dbpilot.connections.connectors.sql import (
    QueryTimeoutError,
    WriteStoppedError,
    commit,
    confirm_read_only,
    estimate_scan,
    is_timeout,
    run_query,
    run_statements,
    server_version,
)
from dbpilot.connections.connectors.targets import SqlTarget
from dbpilot.connections.connectors.values import decode_value, encode_value, kind_of, to_json_value
from dbpilot.demo.sql_sources import build_subscriptions


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return build_subscriptions(tmp_path / "subs.sqlite", dt.date(2026, 6, 15))


def _t(path: Path) -> SqlTarget:
    return SqlTarget(kind="sqlite", sqlite_path=str(path))


def test_read_sessions_report_read_only_and_cap_rows(db: Path) -> None:
    with open_sql(_t(db), 10) as c:
        assert confirm_read_only(c) is True
        assert server_version(c).startswith("SQLite 3")
        result = run_query(c, "SELECT * FROM invoices ORDER BY id", 7)
        assert result.truncated is True and len(result.rows) == 7
        assert [col.name for col in result.columns][:2] == ["id", "account_id"]
        assert result.columns[0].kind == "number"
        assert estimate_scan(c, "SELECT * FROM invoices", ["invoices"], {}) > 100
        assert estimate_scan(c, "SELECT * FROM plans WHERE id = 1", ["plans"], {}) == 0
        # No estimate, no run: the planner's own error is the outcome.
        with pytest.raises(sqlite3.OperationalError):
            estimate_scan(c, "SELEC nonsense", [], {})


def test_the_authorizer_refuses_writes_attach_and_pragmas(db: Path, tmp_path: Path) -> None:
    for sql in (
        "INSERT INTO plans VALUES (9, 'x', 1)",
        "UPDATE plans SET name = 'x'",
        "CREATE TABLE evil (a int)",
        f"ATTACH DATABASE '{tmp_path / 'evil.db'}' AS evil",
        "PRAGMA query_only = 0",
        "PRAGMA writable_schema = 1",
        "SELECT load_extension('x')",
    ):
        with open_sql(_t(db), 10) as c, pytest.raises(Exception):  # noqa: B017
            run_query(c, sql, 10)
    assert not (tmp_path / "evil.db").exists()


def test_a_long_statement_is_interrupted(db: Path) -> None:
    sql = "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT count(*) FROM n"
    with open_sql(_t(db), 1) as c, pytest.raises(QueryTimeoutError):
        run_query(c, sql, 10)
    assert is_timeout(Exception("canceling statement due to statement timeout"))
    assert not is_timeout(Exception("syntax error"))


def test_write_sessions_run_confirmed_statements_and_only_the_caller_commits(db: Path) -> None:
    statements = ["INSERT INTO plans VALUES (5, 'Scale', 1990)", "DELETE FROM plans WHERE id = 1"]
    with open_sql(_t(db), 10, mode="write") as c:
        run_statements(c, statements)  # never committed: the session rolls it back
    with sqlite3.connect(db) as raw:
        assert [r[0] for r in raw.execute("SELECT id FROM plans ORDER BY id")] == [1, 2, 3, 4]
    with open_sql(_t(db), 10, mode="write") as c:
        out = run_statements(c, statements)
        commit(c)
    assert [o.rows_affected for o in out] == [1, 1]
    with sqlite3.connect(db) as raw:
        assert [r[0] for r in raw.execute("SELECT id FROM plans ORDER BY id")] == [2, 3, 4, 5]


def test_a_stopped_write_runs_no_further_statement(db: Path) -> None:
    calls: list[int] = []

    def stopped() -> bool:
        calls.append(1)
        return len(calls) > 1  # the time limit passes after the first statement

    with (
        open_sql(_t(db), 10, mode="write") as c,
        pytest.raises(WriteStoppedError, match="statement 2"),
    ):
        run_statements(
            c, ["DELETE FROM plans WHERE id = 1", "DELETE FROM plans WHERE id = 2"], stopped
        )
    with sqlite3.connect(db) as raw:
        assert raw.execute("SELECT count(*) FROM plans").fetchone()[0] == 4


def test_discovery_reads_columns_keys_indexes_and_counts(db: Path) -> None:
    with open_sql(_t(db), 10) as c:
        tables = {t.name: t for t in discover(c, sample_values=True)}
    assert set(tables) == {"plans", "accounts", "invoices"}
    invoices = tables["invoices"]
    assert invoices.row_count and invoices.schema is None and invoices.qualified == "invoices"
    assert invoices.foreign_keys[0].ref_table == "accounts"
    assert invoices.indexes[0].columns == ["issued_on"]
    status = next(col for col in tables["accounts"].columns if col.name == "status")
    assert set(status.sample_values) <= {"active", "trial", "churned"}
    assert next(col for col in invoices.columns if col.name == "id").primary_key is True


def test_dump_verify_and_restore_round_trip(db: Path, tmp_path: Path) -> None:
    archive = tmp_path / "backup.gz"
    with open_sql(_t(db), 30) as c:
        summary = dump(c, archive, schema=None, max_rows=100_000)
    assert summary.engine == "sqlite" and summary.tables["plans"] == 4
    check = verify(archive)
    assert check.ok is True and check.tables == summary.tables
    assert len(sha256_file(archive)) == 64
    target = tmp_path / "restored.sqlite"
    target.touch()
    stopped_target = tmp_path / "stopped.sqlite"
    stopped_target.touch()
    with open_sql(_t(stopped_target), 30, mode="write") as c, pytest.raises(WriteStoppedError):
        restore(c, archive, lambda: True)
    with sqlite3.connect(stopped_target) as raw:
        assert (
            raw.execute("SELECT count(*) FROM sqlite_master WHERE type = 'table'").fetchone()[0]
            == 0
        )
    with open_sql(_t(target), 30, mode="write") as c:
        result = restore(c, archive)
        commit(c)
    assert result.mismatches == {} and result.tables == summary.tables
    with sqlite3.connect(target) as raw:
        assert (
            raw.execute("SELECT count(*) FROM invoices").fetchone()[0] == summary.tables["invoices"]
        )
        assert raw.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()


def test_a_damaged_or_foreign_file_does_not_verify(tmp_path: Path) -> None:
    bad = tmp_path / "bad.gz"
    bad.write_bytes(b"not gzip")
    assert verify(bad).ok is False
    other = tmp_path / "other.gz"
    with gzip.open(other, "wt") as f:
        f.write(json.dumps({"format": "something-else"}) + "\n")
    assert "not a DBPilot dump" in verify(other).detail
    partial = tmp_path / "partial.gz"
    with gzip.open(partial, "wt") as f:
        f.write(json.dumps({"format": "dbpilot-dump/1"}) + "\n")
        f.write(json.dumps({"t": "table", "name": "x"}) + "\n")
    assert "incomplete" in verify(partial).detail
    lying = tmp_path / "lying.gz"
    with gzip.open(lying, "wt") as f:
        f.write(json.dumps({"format": "dbpilot-dump/1"}) + "\n")
        f.write(json.dumps({"t": "rows", "table": "x", "rows": [[1]]}) + "\n")
        f.write(json.dumps({"t": "end", "tables": {"x": 5}}) + "\n")
    assert "do not match" in verify(lying).detail


def test_a_dump_is_too_large_past_the_row_limit(db: Path, tmp_path: Path) -> None:
    from dbpilot.connections.connectors.dump import BackupTooLargeError

    with open_sql(_t(db), 30) as c, pytest.raises(BackupTooLargeError):
        dump(c, tmp_path / "x.gz", schema=None, max_rows=10)


def test_a_restore_refuses_another_engine_s_dump(db: Path, tmp_path: Path) -> None:
    archive = tmp_path / "pg.gz"
    with gzip.open(archive, "wt") as f:
        f.write(json.dumps({"format": "dbpilot-dump/1", "engine": "postgresql"}) + "\n")
    target = tmp_path / "t.sqlite"
    target.touch()
    with (
        open_sql(_t(target), 10, mode="write") as c,
        pytest.raises(ValueError, match="postgresql dump"),
    ):
        restore(c, archive)


def test_values_encode_losslessly_and_render_for_display() -> None:
    values = [
        None,
        True,
        3,
        "x",
        1.5,
        float("inf"),
        decimal.Decimal("12.50"),
        dt.datetime(2026, 1, 2, 3, 4),
        dt.date(2026, 1, 2),
        dt.time(3, 4),
        dt.timedelta(seconds=90),
        uuid.UUID(int=1),
        b"\x00\x01",
        [1, decimal.Decimal("2.5")],
        {"k": dt.date(2026, 1, 1)},
    ]
    for v in values:
        back = decode_value(encode_value(v))
        if isinstance(v, float) and v == float("inf"):
            assert back == v
        else:
            assert back == v, v
    assert to_json_value(decimal.Decimal("1e20")) == "1E+20"
    assert to_json_value(b"abc") == "<3 bytes>" and to_json_value(float("nan")) == "nan"
    assert to_json_value({"a": [dt.date(2026, 1, 1)]}) == {"a": ["2026-01-01"]}
    assert kind_of([1, 2.5]) == "number" and kind_of([True]) == "boolean" and kind_of([]) == "other"
    assert (
        kind_of([dt.datetime(2026, 1, 1)]) == "datetime"
        and kind_of([dt.date(2026, 1, 1)]) == "date"
    )
    assert kind_of(["a", 1]) == "other" and kind_of(["a"]) == "text"


def test_sqlite_health_reports_size_and_the_file_note(db: Path) -> None:
    with open_sql(_t(db), 10) as c:
        report = sql_health(c)
    assert report.reachable and report.read_only_session is True and (report.size_bytes or 0) > 0
    assert report.security[0].id == "sqlite.file" and report.security[0].status == "info"

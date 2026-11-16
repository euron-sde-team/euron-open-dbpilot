"""The scan limit's arithmetic (AC2) and the restore's handling of untrusted names, without
a database server: plan readers for PostgreSQL, MySQL and SQL Server, the Redis reader's
element counts, and the SQL the dump writes for schemas and sequences."""

from __future__ import annotations

from typing import Any

import pytest
from defusedxml import EntitiesForbidden
from sqlalchemy.dialects import mssql, postgresql

from dbpilot.connections.connectors.dump import _create_schema, _literal
from dbpilot.connections.connectors.redis_source import RedisCommandError, RedisReader
from dbpilot.connections.connectors.sql import (
    ScanUnknownError,
    _mssql_plan_rows,
    _mysql_plan_rows,
    _pg_plan_rows,
)


class FakePg:
    """Answers the reltuples lookup a sequential scan makes, and records what it asked."""

    def __init__(self, reltuples: dict[tuple[str, str], int]) -> None:
        self.reltuples = reltuples
        self.asked: list[tuple[str, str]] = []

    def execute(self, _stmt: Any, params: dict[str, str]) -> Any:
        key = (params["schema"], params["rel"])
        self.asked.append(key)
        value = self.reltuples.get(key, 0)

        class Result:
            def scalar(self) -> int:
                return value

        return Result()


def _seq(rel: str, rows: int, schema: str | None = "public") -> dict[str, Any]:
    node: dict[str, Any] = {"Node Type": "Seq Scan", "Relation Name": rel, "Plan Rows": rows}
    if schema:
        node["Schema"] = schema
    return node


def test_postgres_counts_the_whole_relation_in_its_own_schema() -> None:
    conn = FakePg({("sales", "orders"): 20_000})
    # A filter that keeps one row still reads all 20,000; the schema comes from VERBOSE.
    assert _pg_plan_rows(conn, _seq("orders", 1, "sales")) == 20_000  # type: ignore[arg-type]
    assert conn.asked == [("sales", "orders")]


def test_postgres_multiplies_the_inner_side_of_a_nested_loop() -> None:
    conn = FakePg({("public", "small"): 300})
    inner = {"Node Type": "Materialize", "Plan Rows": 300, "Plans": [_seq("small", 300)]}
    plan = {
        "Node Type": "Nested Loop",
        "Plan Rows": 27_000_000,
        "Plans": [
            {"Node Type": "Nested Loop", "Plan Rows": 90_000, "Plans": [_seq("small", 300), inner]},
            inner,
        ],
    }
    # The plan PostgreSQL 16 makes for a triple cross join of a 300-row table: the old sum
    # said 900 rows; the work is 300 + 300 x 300 + 90,000 x 300.
    assert _pg_plan_rows(conn, plan) == 300 + 90_000 + 27_000_000  # type: ignore[arg-type]
    hashed = {
        "Node Type": "Hash Join",
        "Plan Rows": 300,
        "Plans": [_seq("small", 300), _seq("small", 300)],
    }
    assert _pg_plan_rows(conn, hashed) == 600  # type: ignore[arg-type]


def test_postgres_counts_a_correlated_subquery_once_per_row() -> None:
    conn = FakePg({("public", "customers"): 300, ("public", "orders"): 2500})
    # SELECT (SELECT count(*) FROM orders o WHERE o.customer_id = c.id) FROM customers c
    subplan = {
        "Node Type": "Aggregate",
        "Parent Relationship": "SubPlan",
        "Plan Rows": 1,
        "Plans": [{**_seq("orders", 8), "Parent Relationship": "Outer"}],
    }
    plan = {**_seq("customers", 300), "Plans": [subplan]}
    assert _pg_plan_rows(conn, plan) == 300 + 300 * 2500  # type: ignore[arg-type]
    once = {**plan, "Plans": [{**subplan, "Parent Relationship": "InitPlan"}]}
    assert _pg_plan_rows(conn, once) == 300 + 2500  # type: ignore[arg-type]


MYSQL_COLUMNS = ["id", "select_type", "table", "type", "rows", "filtered"]


def test_mysql_multiplies_the_tables_of_one_select() -> None:
    cross = [(1, "SIMPLE", t, "ALL", 2500, 100.0) for t in ("a", "b", "c")]
    assert _mysql_plan_rows(MYSQL_COLUMNS, cross) == 2500 + 2500**2 + 2500**3
    fk_join = [(1, "SIMPLE", "o", "ALL", 2500, 100.0), (1, "SIMPLE", "c", "eq_ref", 1, 100.0)]
    assert _mysql_plan_rows(MYSQL_COLUMNS, fk_join) == 5000
    filtered = [(1, "SIMPLE", "o", "ALL", 2500, 10.0), (1, "SIMPLE", "i", "ref", 4, 100.0)]
    assert _mysql_plan_rows(MYSQL_COLUMNS, filtered) == 2500 + 250 * 4
    subquery = [(1, "PRIMARY", "a", "ALL", 100, 100.0), (2, "SUBQUERY", "b", "ALL", 100, 100.0)]
    assert _mysql_plan_rows(MYSQL_COLUMNS, subquery) == 200  # separate SELECTs add up
    with pytest.raises(ScanUnknownError):
        _mysql_plan_rows(["id", "table"], [(1, "a")])


SHOWPLAN = """<?xml version="1.0" encoding="utf-16"?>
<ShowPlanXML xmlns="http://schemas.microsoft.com/sqlserver/2004/07/showplan" Version="1.564">
 <BatchSequence><Batch><Statements><StmtSimple StatementType="SELECT"><QueryPlan>
  <RelOp NodeId="0" PhysicalOp="Nested Loops" LogicalOp="Inner Join" EstimateRows="2500">
   <NestedLoops>
    <RelOp NodeId="1" PhysicalOp="Clustered Index Scan" EstimateRows="2500"
           TableCardinality="2500" EstimatedRowsRead="2500"/>
    <RelOp NodeId="2" PhysicalOp="Clustered Index Seek" EstimateRows="1"
           EstimatedRowsRead="1" EstimateRebinds="2499" EstimateRewinds="0"/>
    <RelOp NodeId="3" PhysicalOp="Table Scan" EstimateRows="300" TableCardinality="300"
           EstimateRebinds="0" EstimateRewinds="9"/>
   </NestedLoops>
  </RelOp>
 </QueryPlan></StmtSimple></Statements></Batch></BatchSequence>
</ShowPlanXML>"""


def test_sql_server_counts_scans_seeks_and_their_executions() -> None:
    # 2,500 scanned once, one row sought 2,500 times, 300 scanned 10 times.
    assert _mssql_plan_rows(SHOWPLAN.replace(' encoding="utf-16"', "")) == 2500 + 2500 + 3000


def test_a_plan_with_entities_is_not_parsed() -> None:
    evil = (
        '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]>'
        '<ShowPlanXML><RelOp PhysicalOp="Table Scan" TableCardinality="&e;"/></ShowPlanXML>'
    )
    with pytest.raises(EntitiesForbidden):
        _mssql_plan_rows(evil)


class FakeRedis:
    sizes = {
        "HLEN": {"big_hash": 50_000, "small_hash": 3},
        "LLEN": {"big_list": 1_000_000},
        "ZCARD": {"board": 200_000},
        "SCARD": {"s1": 10, "s2": 20},
        "XLEN": {"events": 9_000},
    }

    def __init__(self) -> None:
        self.sent: list[tuple[Any, ...]] = []

    def execute_command(self, command: str, *args: Any) -> Any:
        self.sent.append((command, *args))
        if command in self.sizes:
            return self.sizes[command][args[0]]
        return b"value"

    def dbsize(self) -> int:
        return 5_000

    def type(self, _key: str) -> bytes:
        return b"list"


@pytest.mark.parametrize(
    ("command", "args", "reads"),
    [
        ("SCAN", ["0", "COUNT", "100000000"], 100_000_000),
        ("SCAN", ["0", "MATCH", "user:*"], 10),
        ("HSCAN", ["big_hash", "0", "count", "500"], 500),
        ("KEYS", ["*"], 5_000),
        ("HGETALL", ["big_hash"], 50_000),
        ("HKEYS", ["small_hash"], 3),
        ("LRANGE", ["big_list", "0", "99"], 100),
        ("LRANGE", ["big_list", "-10", "-1"], 10),
        ("LRANGE", ["big_list", "500000", "500009"], 499_990 + 10),  # walked from the tail
        ("LRANGE", ["big_list", "0", "-1"], 1_000_000),
        ("ZRANGE", ["board", "0", "9"], 10),
        ("ZRANGE", ["board", "0", "-1"], 200_000),
        ("ZRANGE", ["board", "-inf", "+inf", "BYSCORE", "LIMIT", "0", "50"], 50),
        ("ZRANGEBYSCORE", ["board", "-inf", "+inf"], 200_000),
        ("ZREVRANGEBYLEX", ["board", "+", "-", "LIMIT", "10", "20"], 30),
        ("XRANGE", ["events", "-", "+", "COUNT", "25"], 25),
        ("XRANGE", ["events", "-", "+"], 9_000),
        ("SINTER", ["s1", "s2"], 30),
        ("SINTERCARD", ["2", "s1", "s2", "LIMIT", "5"], 30),
        ("SRANDMEMBER", ["s1", "-5"], 5),
        ("SORT_RO", ["big_list"], 1_000_000),
        ("GET", ["k"], 0),
    ],
)
def test_redis_measures_what_a_command_reads(command: str, args: list[str], reads: int) -> None:
    reader = RedisReader(FakeRedis())  # type: ignore[arg-type]
    assert reader.elements_read(command, args) == reads


def test_redis_refuses_past_the_scan_limit_before_sending() -> None:
    client = FakeRedis()
    reader = RedisReader(client)  # type: ignore[arg-type]
    for command, args in (("SCAN", ["0", "COUNT", "100000000"]), ("HGETALL", ["big_hash"])):
        with pytest.raises(RedisCommandError) as err:
            reader.run(command, args, max_scan_keys=1_000)
        assert err.value.code == "scan_limit"
    assert not any(sent[0] in ("SCAN", "HGETALL") for sent in client.sent)
    assert reader.run("LRANGE", ["big_list", "0", "99"], max_scan_keys=1_000).value == "value"


class Stub:
    def __init__(self, dialect: Any) -> None:
        self.dialect = dialect


def test_schema_names_from_the_source_are_quoted_not_trusted() -> None:
    hostile = 'x"; DROP TABLE accounts; --'
    pg = _create_schema(Stub(postgresql.dialect()), hostile)  # type: ignore[arg-type]
    assert pg == 'CREATE SCHEMA IF NOT EXISTS "x""; DROP TABLE accounts; --"'
    tsql = _create_schema(Stub(mssql.dialect()), "x]); DROP TABLE a; --'")  # type: ignore[arg-type]
    assert tsql == (
        "IF SCHEMA_ID(N'x]); DROP TABLE a; --''') IS NULL "
        "EXEC(N'CREATE SCHEMA [x]]); DROP TABLE a; --'']')"
    )
    assert _literal("it's") == "'it''s'"

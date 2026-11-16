"""MongoDB and Redis: their own read-only explorer and query surfaces (AC11), with every
write refused before it is sent (AC3: they have no read-only session flag, so the surface
itself is read-only). Skipped when the demo containers are not running."""

from __future__ import annotations

from typing import Any

import pymongo
import pytest
import redis

from dbpilot.demo import nosql_sources
from tests.integration.conftest import (
    HOST,
    MONGO_PORT,
    READER_PASSWORD,
    REDIS_SOURCE_PORT,
    SOURCE_PASSWORD,
    Stack,
    reachable,
)

pytestmark = pytest.mark.integration
mongo_only = pytest.mark.skipif(
    not reachable(MONGO_PORT), reason="the MongoDB demo source is not running"
)
redis_only = pytest.mark.skipif(
    not reachable(REDIS_SOURCE_PORT), reason="the Redis demo source is not running"
)
MONGO_ADMIN = f"mongodb://catalog_admin:{SOURCE_PASSWORD}@{HOST}:{MONGO_PORT}/?authSource=admin"
REDIS_URL = f"redis://:{SOURCE_PASSWORD}@{HOST}:{REDIS_SOURCE_PORT}/0"


@pytest.fixture(scope="module")
def catalog() -> None:
    nosql_sources.build_catalog(MONGO_ADMIN, "catalog_test", READER_PASSWORD)


@pytest.fixture(scope="module")
def sessions() -> None:
    nosql_sources.build_sessions(REDIS_URL)


def _mongo(**extra: Any) -> dict[str, Any]:
    return {
        "name": "Catalog",
        "kind": "mongodb",
        "host": HOST,
        "port": MONGO_PORT,
        "database": "catalog_test",
        "username": "dbpilot_reader",
        "password": READER_PASSWORD,
        "auth_source": "catalog_test",
        **extra,
    }


def _redis(**extra: Any) -> dict[str, Any]:
    return {
        "name": "Sessions",
        "kind": "redis",
        "host": HOST,
        "port": REDIS_SOURCE_PORT,
        "database": "0",
        "password": SOURCE_PASSWORD,
        **extra,
    }


async def _mq(stack: Stack, cid: str, **body: Any) -> dict[str, Any]:
    return dict(await stack.ok("POST", stack.p("/queries/mongodb"), {"connection_id": cid, **body}))


async def _rq(stack: Stack, cid: str, command: str, *args: str) -> dict[str, Any]:
    return dict(
        await stack.ok(
            "POST",
            stack.p("/queries/redis"),
            {"connection_id": cid, "command": command, "args": list(args)},
        )
    )


@mongo_only
async def test_mongodb_explorer_queries_and_refusals(ready: Stack, catalog: None) -> None:
    conn = await ready.connection(_mongo(max_rows=50))
    test = await ready.ok("POST", ready.p(f"/connections/{conn['id']}/test"))
    assert test["ok"] is True and test["read_only"] is None and test["objects"] == 2
    assert test["server_version"].startswith("MongoDB 7")
    schema = await ready.ok("GET", ready.p(f"/connections/{conn['id']}/schema"))
    products = next(o for o in schema["objects"] if o["name"] == "products")
    assert products["kind"] == "collection" and products["row_count"] == 240
    assert {"_id", "name", "category", "price"} <= {c["name"] for c in products["columns"]}
    assert any(i["columns"] == ["category"] for i in products["indexes"])
    found = await _mq(
        ready,
        conn["id"],
        collection="products",
        filter={"category": "office", "price": {"$gt": 50}},
        sort=[["price", -1]],
        limit=5,
    )
    assert found["status"] == "succeeded" and 0 < found["row_count"] <= 5
    assert "price" in [c["name"] for c in found["columns"]]
    many = await _mq(ready, conn["id"], collection="reviews")
    assert many["row_count"] == 50 and many["truncated"] is True
    agg = await _mq(
        ready,
        conn["id"],
        collection="reviews",
        operation="aggregate",
        pipeline=[{"$group": {"_id": "$rating", "n": {"$sum": 1}}}, {"$sort": {"_id": 1}}],
    )
    assert agg["status"] == "succeeded" and agg["row_count"] == 5
    count = await _mq(
        ready, conn["id"], collection="products", operation="count", filter={"in_stock": True}
    )
    assert count["page"]["rows"][0][0] > 100
    distinct = await _mq(
        ready, conn["id"], collection="products", operation="distinct", field="category"
    )
    assert distinct["row_count"] == 6
    before = pymongo.MongoClient(MONGO_ADMIN)["catalog_test"]["products"].count_documents({})
    for body, code in (
        (
            {"operation": "aggregate", "pipeline": [{"$match": {}}, {"$out": "stolen"}]},
            "write_or_script",
        ),
        (
            {"operation": "aggregate", "pipeline": [{"$merge": {"into": "products"}}]},
            "write_or_script",
        ),
        ({"filter": {"$where": "this.price > 1"}}, "write_or_script"),
        (
            {"filter": {"name": {"$function": {"body": "x", "args": [], "lang": "js"}}}},
            "write_or_script",
        ),
        ({"operation": "aggregate", "pipeline": [{"$currentOp": {}}]}, "write_or_script"),
    ):
        out = await _mq(ready, conn["id"], collection="products", **body)
        assert out["status"] == "refused" and out["error_code"] == code, out
    assert "stolen" not in pymongo.MongoClient(MONGO_ADMIN)["catalog_test"].list_collection_names()
    assert (
        pymongo.MongoClient(MONGO_ADMIN)["catalog_test"]["products"].count_documents({}) == before
    )
    insert = await ready.client.post(
        ready.p("/queries/mongodb"),
        json={"connection_id": conn["id"], "collection": "products", "operation": "insert"},
    )
    assert insert.status_code == 422  # not an operation this surface has
    scan = await ready.connection(_mongo(name="Tight", max_scan_rows=10))
    limited = await _mq(ready, scan["id"], collection="reviews", filter={"rating": 5})
    assert limited["status"] == "scan_limit" and limited["estimated_scan_rows"] == 1200
    indexed = await _mq(ready, scan["id"], collection="products", filter={"category": "office"})
    assert indexed["status"] == "succeeded"  # the index bounds the read
    card = await ready.ok("GET", ready.p(f"/connections/{conn['id']}/health"))
    auth = next(c for c in card["security"] if c["id"] == "mongodb.auth")
    assert auth["status"] == "pass" and card["reachable"] is True
    for path, body in (("/ai/sql", {"question": "x"}), ("/writes", {"sql": "x"}), ("/backups", {})):
        resp = await ready.client.post(ready.p(path), json={"connection_id": conn["id"], **body})
        assert resp.status_code == 422 and resp.json()["error"]["code"] == "engine_not_supported", (
            path
        )
    preview = await ready.ok(
        "POST", ready.p(f"/connections/{conn['id']}/preview"), {"object": "products", "limit": 3}
    )
    assert preview["row_count"] == 3


@redis_only
async def test_redis_explorer_commands_and_refusals(ready: Stack, sessions: None) -> None:
    conn = await ready.connection(_redis(max_rows=20))
    test = await ready.ok("POST", ready.p(f"/connections/{conn['id']}/test"))
    assert test["ok"] is True and test["read_only"] is None and test["objects"] > 190
    schema = await ready.ok("GET", ready.p(f"/connections/{conn['id']}/schema"))
    groups = {o["name"]: o for o in schema["objects"]}
    assert groups["session:*"]["details"]["types"] == {"hash": 120}
    assert groups["session:*"]["details"]["with_ttl"] == 120
    hash_ = await _rq(ready, conn["id"], "HGETALL", "session:00001")
    assert {r[0] for r in hash_["page"]["rows"]} == {"user_id", "ip", "agent"}
    assert (await _rq(ready, conn["id"], "get", "config:maintenance"))["page"]["rows"] == [["off"]]
    assert (await _rq(ready, conn["id"], "TYPE", "cart:1"))["page"]["rows"] == [["list"]]
    scan = await _rq(ready, conn["id"], "SCAN", "0", "MATCH", "rate:*", "COUNT", "100")
    assert scan["status"] == "succeeded"
    zr = await _rq(ready, conn["id"], "ZRANGE", "leaderboard:weekly", "0", "-1")
    assert zr["row_count"] == 20 and zr["truncated"] is True
    r = redis.Redis.from_url(REDIS_URL)
    keys_before = r.dbsize()
    for command, args in (
        ("SET", ["config:maintenance", "on"]),
        ("DEL", ["cart:1"]),
        ("FLUSHALL", []),
        ("FLUSHDB", []),
        ("EVAL", ["return redis.call('FLUSHALL')", "0"]),
        ("CONFIG", ["SET", "requirepass", ""]),
        ("SELECT", ["1"]),
        ("MULTI", []),
        ("SORT", ["cart:1", "STORE", "stolen"]),
        ("OBJECT", ["HELP"]),
        ("EXPIRE", ["cart:1", "1"]),
    ):
        out = await _rq(ready, conn["id"], command, *args)
        assert out["status"] == "refused" and out["error_code"] == "not_a_read", command
    assert (
        r.get("config:maintenance") == b"off" and r.dbsize() == keys_before and r.exists("cart:1")
    )
    r.close()
    tight = await ready.connection(_redis(name="Tight", max_scan_rows=5))
    keys = await _rq(ready, tight["id"], "KEYS", "*")
    assert keys["status"] == "scan_limit"
    # Every command is measured before it is sent, not only KEYS.
    for command, args in (
        ("SCAN", ["0", "COUNT", "100000000"]),
        ("ZRANGE", ["leaderboard:weekly", "0", "-1"]),
        ("LRANGE", ["cart:1", "0", "-1"]),
    ):
        out = await _rq(ready, tight["id"], command, *args)
        if command == "LRANGE" and redis.Redis.from_url(REDIS_URL).llen("cart:1") <= 5:
            assert out["status"] == "succeeded"
            continue
        assert out["status"] == "scan_limit" and out["error_code"] == "scan_limit", (command, out)
    assert (await _rq(ready, tight["id"], "ZRANGE", "leaderboard:weekly", "0", "2"))[
        "row_count"
    ] == 3
    assert (await _rq(ready, tight["id"], "HGETALL", "session:00001"))["status"] == "succeeded"
    card = await ready.ok("GET", ready.p(f"/connections/{conn['id']}/health"))
    assert next(c for c in card["security"] if c["id"] == "redis.requirepass")["status"] == "pass"
    assert card["size_bytes"] > 0
    resp = await ready.client.post(
        ready.p("/ai/sql"), json={"connection_id": conn["id"], "question": "x"}
    )
    assert resp.status_code == 422

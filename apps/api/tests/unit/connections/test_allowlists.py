"""MongoDB and Redis have no read-only session: their surfaces are read-only by allowlist,
checked before anything is sent."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from bson import ObjectId

from dbpilot.connections.connectors.mongo import (
    MongoRequest,
    MongoRequestError,
    check_request,
    infer_fields,
    parse_json_object,
    to_plain,
)
from dbpilot.connections.connectors.redis_source import RedisCommandError, _text, check_command


def _req(**kw: Any) -> MongoRequest:
    return MongoRequest(**{"collection": "products", "operation": "find", **kw})


@pytest.mark.parametrize(
    ("kw", "code"),
    [
        ({"operation": "insert"}, "not_a_read"),
        ({"operation": "aggregate", "pipeline": [{"$out": "x"}]}, "write_or_script"),
        ({"operation": "aggregate", "pipeline": [{"$merge": {"into": "x"}}]}, "write_or_script"),
        ({"operation": "aggregate", "pipeline": [{"$currentOp": {}}]}, "write_or_script"),
        (
            {
                "operation": "aggregate",
                "pipeline": [{"$lookup": {"from": "y", "pipeline": [{"$out": "z"}], "as": "j"}}],
            },
            "write_or_script",
        ),
        ({"filter": {"$where": "1"}}, "write_or_script"),
        ({"filter": {"a": {"$function": {}}}}, "write_or_script"),
        ({"projection": {"x": {"$accumulator": {}}}}, "write_or_script"),
        ({"operation": "aggregate", "pipeline": []}, "empty_pipeline"),
        ({"operation": "aggregate", "pipeline": [{"$match": {}, "$limit": 1}]}, "bad_stage"),
        ({"operation": "distinct"}, "missing_field"),
        ({"collection": "system.users"}, "bad_collection"),
        ({"collection": "a$b"}, "bad_collection"),
        ({"skip": -1}, "bad_limit"),
    ],
)
def test_mongo_requests_that_could_write_are_refused(kw: dict[str, Any], code: str) -> None:
    with pytest.raises(MongoRequestError) as err:
        check_request(_req(**kw))
    assert err.value.code == code


def test_mongo_reads_pass_and_extended_json_decodes() -> None:
    check_request(_req(filter={"price": {"$gt": 5}}, sort=[("price", -1)], limit=5))
    check_request(
        _req(operation="aggregate", pipeline=[{"$match": {"a": 1}}, {"$group": {"_id": "$b"}}])
    )
    check_request(_req(operation="distinct", field_name="category"))
    nested: dict[str, Any] = {"a": 1}
    for _ in range(40):
        nested = {"x": nested}
    with pytest.raises(MongoRequestError):
        check_request(_req(filter=nested))
    decoded = parse_json_object({"_id": {"$oid": "65a1b2c3d4e5f60718293a4b"}})
    assert isinstance(decoded["_id"], ObjectId)
    assert parse_json_object(None) is None


def test_mongo_documents_come_back_as_plain_json() -> None:
    plain = to_plain(
        {"_id": ObjectId("65a1b2c3d4e5f60718293a4b"), "at": dt.datetime(2026, 1, 2), "n": 1}
    )
    assert plain["_id"] == {"$oid": "65a1b2c3d4e5f60718293a4b"}
    assert plain["at"] == {"$date": "2026-01-02T00:00:00Z"} and plain["n"] == 1
    fields = infer_fields([{"_id": 1, "a": "x"}, {"_id": 2, "a": None, "b": 2.5}])
    by = {f["name"]: f for f in fields}
    assert fields[0]["name"] == "_id" and by["_id"]["primary_key"] is True
    assert (
        by["a"]["nullable"] is True and by["b"]["nullable"] is True and by["b"]["type"] == "float"
    )


@pytest.mark.parametrize(
    ("command", "args"),
    [
        ("SET", ["k", "v"]),
        ("DEL", ["k"]),
        ("FLUSHALL", []),
        ("FLUSHDB", []),
        ("EVAL", ["x", "0"]),
        ("EVALSHA", ["x", "0"]),
        ("CONFIG", ["GET", "*"]),
        ("SELECT", ["1"]),
        ("MULTI", []),
        ("SORT", ["k"]),
        ("GEORADIUS", ["k", "0", "0", "1", "km"]),
        ("XREADGROUP", []),
        ("OBJECT", ["HELP"]),
        ("MEMORY", ["DOCTOR"]),
        ("XINFO", ["HELP"]),
        ("OBJECT", []),
        ("SUBSCRIBE", ["c"]),
        ("SHUTDOWN", []),
        ("", []),
    ],
)
def test_redis_commands_that_are_not_reads_are_refused(command: str, args: list[str]) -> None:
    with pytest.raises(RedisCommandError) as err:
        check_command(command, args)
    assert err.value.code == "not_a_read"


def test_redis_reads_pass_and_stores_are_refused() -> None:
    assert check_command("get", ["k"]) == ("GET", ["k"])
    assert check_command("OBJECT", ["encoding", "k"])[0] == "OBJECT"
    assert check_command("SORT_RO", ["k"])[0] == "SORT_RO"
    with pytest.raises(RedisCommandError):
        check_command(
            "GEOSEARCH", ["k", "FROMLONLAT", "0", "0", "BYRADIUS", "1", "km", "STORE", "x"]
        )
    with pytest.raises(RedisCommandError) as err:
        check_command("MGET", ["k"] * 65)
    assert err.value.code == "too_long"
    assert _text(b"\xff\x00") == {"$base64": "/wA="}
    assert _text([b"a", {b"k": b"v"}, 1, None, 2.5]) == ["a", {"k": "v"}, 1, None, 2.5]

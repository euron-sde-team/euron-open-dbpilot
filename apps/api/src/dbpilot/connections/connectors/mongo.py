"""MongoDB: a read-only surface by construction.

MongoDB has no read-only session flag, so read-only is enforced by what exists here.
``MongoReader`` wraps the driver's client without exposing it and offers reads only:
``find``, ``aggregate``, ``count``, ``distinct`` and the catalog reads discovery and the
health card need. There is no insert, update, delete, drop or generic command method, so no
request can travel to a write. Before any of them runs, ``check_request`` refuses every
operator that writes or runs server-side JavaScript anywhere in the request tree
(``$out``, ``$merge``, ``$where``, ``$function``, ``$accumulator``) and every aggregation
stage outside a read-only allowlist. Writes to MongoDB are not offered in 0.1.0 at all.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from bson import json_util
from pymongo import MongoClient

MongoOperation = Literal["find", "aggregate", "count", "distinct"]
OPERATIONS = ("find", "aggregate", "count", "distinct")
MAX_REQUEST_DEPTH = 32
SAMPLE_DOCS = 50

# Aggregation stages that only read. Everything else ($out, $merge, $currentOp,
# $listSessions, $planCacheStats, ...) is refused.
READ_STAGES = frozenset(
    {
        "$match",
        "$project",
        "$group",
        "$sort",
        "$limit",
        "$skip",
        "$unwind",
        "$lookup",
        "$count",
        "$addFields",
        "$set",
        "$unset",
        "$facet",
        "$bucket",
        "$bucketAuto",
        "$sortByCount",
        "$replaceRoot",
        "$replaceWith",
        "$sample",
        "$graphLookup",
        "$unionWith",
        "$redact",
        "$densify",
        "$fill",
        "$setWindowFields",
    }
)
# Operators refused anywhere in a filter, projection or pipeline: writes and server-side
# JavaScript.
DENIED_OPERATORS = frozenset({"$out", "$merge", "$where", "$function", "$accumulator"})
_COLLECTION = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]{0,119}$")


class MongoRequestError(ValueError):
    """The request is not a read this surface allows. ``code`` names the rule."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass
class MongoRequest:
    collection: str
    operation: MongoOperation
    filter: dict[str, Any] = field(default_factory=dict)
    projection: dict[str, Any] | None = None
    sort: list[tuple[str, int]] = field(default_factory=list)
    skip: int = 0
    limit: int | None = None
    pipeline: list[dict[str, Any]] = field(default_factory=list)
    field_name: str | None = None


def _walk(value: Any, depth: int = 0) -> None:
    if depth > MAX_REQUEST_DEPTH:
        raise MongoRequestError("the request is nested too deeply", "too_deep")
    if isinstance(value, dict):
        for key, inner in value.items():
            if str(key) in DENIED_OPERATORS:
                raise MongoRequestError(
                    f"{key} is not allowed: this surface only reads", "write_or_script"
                )
            _walk(inner, depth + 1)
    elif isinstance(value, list):
        for inner in value:
            _walk(inner, depth + 1)


def check_request(req: MongoRequest) -> MongoRequest:
    """Accept ``req`` as a read or raise MongoRequestError (nothing reaches the server)."""
    if req.operation not in OPERATIONS:
        raise MongoRequestError(
            f"only {', '.join(OPERATIONS)} run on MongoDB in DBPilot", "not_a_read"
        )
    if not _COLLECTION.match(req.collection) or req.collection.startswith("system."):
        raise MongoRequestError("that is not a collection this surface reads", "bad_collection")
    _walk(req.filter)
    _walk(req.projection)
    if req.operation == "aggregate":
        if not req.pipeline:
            raise MongoRequestError("an aggregation needs at least one stage", "empty_pipeline")
        for stage in req.pipeline:
            if not isinstance(stage, dict) or len(stage) != 1:
                raise MongoRequestError('each stage is one {"$stage": ...} object', "bad_stage")
            name = next(iter(stage))
            if name not in READ_STAGES:
                raise MongoRequestError(
                    f"the stage {name} is not allowed: only read stages run", "write_or_script"
                )
        _walk(req.pipeline)
    if req.operation == "distinct" and not req.field_name:
        raise MongoRequestError("distinct needs a field", "missing_field")
    if req.skip < 0 or (req.limit is not None and req.limit < 0):
        raise MongoRequestError("skip and limit cannot be negative", "bad_limit")
    return req


def parse_json_object(raw: Any) -> Any:
    """Extended JSON ({"$oid": ...}, {"$date": ...}) into BSON values for a request."""
    return json_util.loads(json_util.dumps(raw)) if raw is not None else None


def to_plain(doc: Any) -> Any:
    """A BSON document as relaxed Extended JSON: plain JSON values, with ObjectIds and dates
    kept recognizable as {"$oid": ...} and {"$date": ...}. Decoded with the standard json
    module on purpose (bson's own decoder would turn them back into BSON types)."""
    return json.loads(json_util.dumps(doc, json_options=json_util.RELAXED_JSON_OPTIONS))


@dataclass
class MongoResult:
    rows: list[Any]
    truncated: bool
    duration_ms: float


class MongoReader:
    """Read access to one MongoDB database. The client is private: no write path exists."""

    def __init__(self, client: MongoClient[dict[str, Any]], database: str, timeout_s: int) -> None:
        self.__client = client
        self.__db = client[database]
        self.database = database
        self.max_time_ms = max(int(timeout_s * 1000), 1)

    # Catalog and health reads.

    def ping(self) -> None:
        self.__client.admin.command("ping")

    def server_version(self) -> str:
        return "MongoDB " + str(self.__client.server_info().get("version", ""))

    def collection_names(self) -> list[str]:
        return sorted(n for n in self.__db.list_collection_names() if not n.startswith("system."))

    def estimated_count(self, collection: str) -> int:
        return int(self.__db[collection].estimated_document_count(maxTimeMS=self.max_time_ms))

    def indexes(self, collection: str) -> list[dict[str, Any]]:
        out = []
        for name, spec in self.__db[collection].index_information().items():
            out.append(
                {
                    "name": name,
                    "columns": [str(k[0]) for k in spec.get("key", [])],
                    "unique": bool(spec.get("unique", False)) or name == "_id_",
                }
            )
        return out

    def sample(self, collection: str, n: int = SAMPLE_DOCS) -> list[dict[str, Any]]:
        cursor = self.__db[collection].find({}, limit=n, max_time_ms=self.max_time_ms)
        return list(cursor)

    def db_stats(self) -> dict[str, Any]:
        return dict(self.__db.command("dbStats"))

    def server_status(self) -> dict[str, Any]:
        return dict(self.__client.admin.command("serverStatus"))

    def connection_status(self) -> dict[str, Any]:
        return dict(self.__client.admin.command("connectionStatus"))

    def profile_level(self) -> int:
        return int(self.__db.command("profile", -1).get("was", 0))

    def slow_operations(self, limit: int = 5) -> list[dict[str, Any]]:
        cursor = self.__db["system.profile"].find(
            {},
            {"op": 1, "ns": 1, "millis": 1, "command": 1, "ts": 1},
            limit=limit,
            sort=[("millis", -1)],
            max_time_ms=self.max_time_ms,
        )
        return [to_plain(d) for d in cursor]

    # Requests (already through check_request).

    def explain_scan(self, req: MongoRequest) -> int | None:
        """Documents the server expects to examine: the collection size when the winning
        plan scans the whole collection, else 0 (an index bounds the read)."""
        coll = self.__db[req.collection]
        if req.operation == "aggregate":
            plan = self.__db.command(
                "aggregate",
                req.collection,
                pipeline=req.pipeline,
                explain=True,
                maxTimeMS=self.max_time_ms,
            )
        else:
            cursor = coll.find(req.filter or {}, max_time_ms=self.max_time_ms)
            plan = cursor.explain()
        return self.estimated_count(req.collection) if "COLLSCAN" in str(plan) else 0

    def run(self, req: MongoRequest, max_rows: int) -> MongoResult:
        started = time.perf_counter()
        coll = self.__db[req.collection]
        cap = max_rows if req.limit is None else min(req.limit, max_rows)
        if req.operation == "find":
            cursor = coll.find(
                req.filter or {},
                req.projection,
                skip=req.skip,
                limit=cap + 1,
                sort=req.sort or None,
                max_time_ms=self.max_time_ms,
            )
            docs = list(cursor)
        elif req.operation == "aggregate":
            pipeline = [*req.pipeline, {"$limit": cap + 1}]
            docs = list(coll.aggregate(pipeline, maxTimeMS=self.max_time_ms))
        elif req.operation == "count":
            n = coll.count_documents(req.filter or {}, maxTimeMS=self.max_time_ms)
            docs = [{"count": n}]
        else:
            values = coll.distinct(
                req.field_name or "", req.filter or {}, maxTimeMS=self.max_time_ms
            )
            docs = [{"value": v} for v in values]
        truncated = len(docs) > cap
        rows = [to_plain(d) for d in docs[:cap]]
        return MongoResult(
            rows=rows,
            truncated=truncated,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
        )


def infer_fields(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fields seen in sampled documents with the BSON types they held (MongoDB has no fixed
    schema; this is what the sample shows, labelled as such)."""
    seen: dict[str, dict[str, int]] = {}
    for doc in docs:
        for key, value in doc.items():
            types = seen.setdefault(str(key), {})
            name = type(value).__name__
            types[name] = types.get(name, 0) + 1
    total = max(len(docs), 1)
    out = []
    for key, types in seen.items():
        present = sum(types.values())
        out.append(
            {
                "name": key,
                "type": "|".join(sorted(types, key=lambda t: -types[t])),
                "nullable": present < total or "NoneType" in types,
                "primary_key": key == "_id",
            }
        )
    return sorted(out, key=lambda f: (f["name"] != "_id", f["name"]))

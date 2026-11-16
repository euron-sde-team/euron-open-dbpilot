"""Schema discovery, snapshots and the snapshot cache.

Discovery runs against the live connection through the audited gateway, read-only, and
every result is kept as a snapshot. One discovery result serves the explorer, autocomplete,
the ER view, the AI assistant's context and schema comparison (AC6).

Caching (docs/architecture/HLD.md): the latest snapshot is found with one indexed lookup
of ``(connection_id, captured_at)``; its objects come from the key-value cache (Redis, or
process memory) under ``schema:<snapshot id>`` for ``SCHEMA_CACHE_TTL_S``, else from the
snapshot row. A refresh writes a new snapshot with a new id, so every reader moves to the
new key at once and the old entry ages out; nothing has to be deleted for a refresh to be
seen everywhere. A snapshot older than ``SCHEMA_MAX_AGE_MINUTES``, or older than a
confirmed DDL write on the connection, is stale: the next read rediscovers from the live
database.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import orjson
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from dbpilot.audit.service import Actor
from dbpilot.connections import gateway
from dbpilot.connections.connectors.discovery import TableInfo, discover
from dbpilot.connections.connectors.mongo import MongoReader, infer_fields
from dbpilot.connections.connectors.redis_source import RedisReader
from dbpilot.connections.models import Connection
from dbpilot.connections.service import friendly
from dbpilot.core import kv, metrics
from dbpilot.core.container import Container
from dbpilot.core.errors import AppError, NotFoundError, UnprocessableError
from dbpilot.core.schemas import decode_cursor, encode_cursor
from dbpilot.schema.models import SchemaSnapshot
from dbpilot.schema.schemas import (
    RelationshipEdge,
    RelationshipNode,
    Relationships,
    SchemaObject,
    SearchHit,
    SnapshotOut,
    SnapshotSummary,
)

CACHE = "schema"
KEEP_SNAPSHOTS = 30
REDIS_SCAN_KEYS = 1000
REDIS_TYPED_KEYS = 200
SQL_ENGINES = ("postgresql", "mysql", "mssql", "sqlite")


def _key(snapshot_id: uuid.UUID) -> str:
    return f"schema:{snapshot_id}"


def _from_table(t: TableInfo) -> SchemaObject:
    d = dataclasses.asdict(t)
    d["schema_name"] = d.pop("schema")
    return SchemaObject.model_validate(d)


def _mongo_objects(reader: MongoReader, max_objects: int) -> list[SchemaObject]:
    out = []
    for name in reader.collection_names()[:max_objects]:
        out.append(
            SchemaObject(
                name=name,
                kind="collection",
                row_count=reader.estimated_count(name),
                columns=infer_fields(reader.sample(name)),
                indexes=reader.indexes(name),
                details={"fields_from": "sampled documents"},
            )
        )
    return out


def _pattern(key: str) -> str:
    return key.split(":", 1)[0] + ":*" if ":" in key else key


def _redis_objects(reader: RedisReader) -> list[SchemaObject]:
    keys = reader.scan_keys(None, 200, REDIS_SCAN_KEYS)
    groups: dict[str, list[str]] = {}
    for key in keys:
        groups.setdefault(_pattern(key), []).append(key)
    typed = 0
    out = []
    for pattern, members in sorted(groups.items()):
        types: dict[str, int] = {}
        with_ttl = 0
        for key in members[: max(REDIS_TYPED_KEYS - typed, 1)]:
            info = reader.key_info(key)
            typed += 1
            types[str(info["type"])] = types.get(str(info["type"]), 0) + 1
            with_ttl += 1 if int(info["ttl_s"]) >= 0 else 0
        out.append(
            SchemaObject(
                name=pattern,
                kind="key_group",
                row_count=len(members),
                details={
                    "types": types,
                    "with_ttl": with_ttl,
                    "sample_keys": members[:5],
                    "sampled_from": len(keys),
                    "database_keys": reader.dbsize(),
                },
            )
        )
    return out


def _work(conn: Connection, max_objects: int) -> Callable[[gateway.Handle], list[SchemaObject]]:
    def work(handle: gateway.Handle) -> list[SchemaObject]:
        if isinstance(handle, MongoReader):
            return _mongo_objects(handle, max_objects)
        if isinstance(handle, RedisReader):
            return _redis_objects(handle)
        tables = discover(
            handle,
            (conn.config or {}).get("default_schema"),
            max_tables=max_objects,
            sample_values=conn.sample_values,
        )
        return [_from_table(t) for t in tables]

    return work


def _summary(row: SchemaSnapshot) -> SnapshotSummary:
    return SnapshotSummary(
        id=row.id,
        connection_id=row.connection_id,
        engine=row.engine,
        object_count=row.object_count,
        column_count=row.column_count,
        duration_ms=row.duration_ms,
        trigger=row.trigger,
        captured_at=row.captured_at,
    )


async def discover_now(
    container: Container,
    session: AsyncSession,
    conn: Connection,
    actor: Actor,
    *,
    org_id: uuid.UUID | None,
    trigger: str,
) -> SnapshotOut:
    """Discover the schema from the live database now and keep it as a snapshot."""
    created: list[SchemaSnapshot] = []
    started = datetime.now(UTC)

    def on_done(objects: list[SchemaObject] | None, error: BaseException | None) -> dict[str, Any]:
        if objects is None:
            return {"trigger": trigger}
        row = SchemaSnapshot(
            id=uuid.uuid4(),
            project_id=conn.project_id,
            connection_id=conn.id,
            engine=conn.kind,
            objects=[o.model_dump() for o in objects],
            object_count=len(objects),
            column_count=sum(len(o.columns) for o in objects),
            duration_ms=round((datetime.now(UTC) - started).total_seconds() * 1000, 1),
            trigger=trigger,
            captured_by=actor.id,
            captured_at=datetime.now(UTC),
        )
        session.add(row)
        created.append(row)
        return {"trigger": trigger, "objects": len(objects), "snapshot_id": str(row.id)}

    try:
        objects = await gateway.read(
            container,
            session,
            conn,
            actor,
            action="schema.discovered",
            work=_work(conn, container.settings.schema_max_tables),
            org_id=org_id,
            on_done=on_done,
            timeout_s=max(conn.timeout_s, 60),
        )
    except AppError:
        metrics.SCHEMA_DISCOVERIES.labels(conn.kind, "failed").inc()
        raise
    except Exception as exc:  # drivers raise many unrelated types
        metrics.SCHEMA_DISCOVERIES.labels(conn.kind, "failed").inc()
        raise AppError(friendly(gateway.error_text(exc)), code="schema_discovery_failed") from exc
    metrics.SCHEMA_DISCOVERIES.labels(conn.kind, "ok").inc()
    row = created[0]
    await _prune(session, conn.id)
    await kv.cache_set(
        container.kv,
        _key(row.id),
        orjson.dumps(row.objects),
        container.settings.schema_cache_ttl_s,
        CACHE,
    )
    return SnapshotOut(**_summary(row).model_dump(), objects=objects, cached=False)


async def _prune(session: AsyncSession, connection_id: uuid.UUID) -> None:
    """Keep the newest snapshots of a connection, and any a comparison still refers to."""
    from dbpilot.comparisons import service as comparisons

    old = (
        await session.scalars(
            select(SchemaSnapshot.id)
            .where(SchemaSnapshot.connection_id == connection_id)
            .order_by(SchemaSnapshot.captured_at.desc(), SchemaSnapshot.id.desc())
            .offset(KEEP_SNAPSHOTS)
        )
    ).all()
    if not old:
        return
    keep = await comparisons.referenced_snapshot_ids(session, list(old))
    doomed = [i for i in old if i not in keep]
    if doomed:
        await session.execute(delete(SchemaSnapshot).where(SchemaSnapshot.id.in_(doomed)))
        await session.commit()


async def latest_row(session: AsyncSession, connection_id: uuid.UUID) -> SchemaSnapshot | None:
    return await session.scalar(
        select(SchemaSnapshot)
        .where(SchemaSnapshot.connection_id == connection_id)
        .order_by(SchemaSnapshot.captured_at.desc(), SchemaSnapshot.id.desc())
        .limit(1)
    )


def is_stale(container: Container, conn: Connection, captured_at: datetime) -> bool:
    age_limit = timedelta(minutes=container.settings.schema_max_age_minutes)
    if datetime.now(UTC) - captured_at > age_limit:
        return True
    return conn.schema_invalidated_at is not None and captured_at < conn.schema_invalidated_at


async def _objects(container: Container, row: SchemaSnapshot) -> tuple[list[SchemaObject], bool]:
    cached = await kv.cache_get(container.kv, _key(row.id), CACHE)
    if cached is not None:
        return [SchemaObject.model_validate(o) for o in orjson.loads(cached)], True
    await kv.cache_set(
        container.kv,
        _key(row.id),
        orjson.dumps(row.objects),
        container.settings.schema_cache_ttl_s,
        CACHE,
    )
    return [SchemaObject.model_validate(o) for o in row.objects], False


async def snapshot_out(container: Container, row: SchemaSnapshot) -> SnapshotOut:
    objects, cached = await _objects(container, row)
    return SnapshotOut(**_summary(row).model_dump(), objects=objects, cached=cached)


async def ensure(
    container: Container,
    session: AsyncSession,
    conn: Connection,
    actor: Actor,
    *,
    org_id: uuid.UUID | None,
) -> SnapshotOut:
    """The latest snapshot when it is fresh, else a live discovery."""
    row = await latest_row(session, conn.id)
    if row is not None and not is_stale(container, conn, row.captured_at):
        return await snapshot_out(container, row)
    return await discover_now(container, session, conn, actor, org_id=org_id, trigger="auto")


async def invalidate(session: AsyncSession, conn: Connection) -> None:
    """Mark the cached schema stale (after a confirmed DDL write); the caller commits."""
    conn.schema_invalidated_at = datetime.now(UTC)


async def list_snapshots(
    session: AsyncSession, conn: Connection, cursor: str | None, limit: int
) -> tuple[list[SnapshotSummary], str | None]:
    """Newest first, keyset-paginated on (captured_at, id)."""
    stmt = select(SchemaSnapshot).where(SchemaSnapshot.connection_id == conn.id)
    after = decode_cursor(cursor)
    if after:
        at, _, last = after.partition("|")
        try:
            at_dt, last_id = datetime.fromisoformat(at), uuid.UUID(last)
            stmt = stmt.where(
                (SchemaSnapshot.captured_at < at_dt)
                | ((SchemaSnapshot.captured_at == at_dt) & (SchemaSnapshot.id < last_id))
            )
        except ValueError:
            pass  # a malformed cursor starts from the top
    rows = list(
        (
            await session.scalars(
                stmt.order_by(SchemaSnapshot.captured_at.desc(), SchemaSnapshot.id.desc()).limit(
                    limit + 1
                )
            )
        ).all()
    )
    nxt = None
    if len(rows) > limit:
        last_row = rows[limit - 1]
        nxt = encode_cursor(f"{last_row.captured_at.isoformat()}|{last_row.id}")
    return [_summary(r) for r in rows[:limit]], nxt


async def get_snapshot(
    container: Container, session: AsyncSession, conn: Connection, snapshot_id: uuid.UUID
) -> SnapshotOut:
    row = await session.scalar(
        select(SchemaSnapshot).where(
            SchemaSnapshot.id == snapshot_id, SchemaSnapshot.connection_id == conn.id
        )
    )
    if row is None:
        raise NotFoundError("Snapshot not found")
    return await snapshot_out(container, row)


async def snapshot_row(session: AsyncSession, snapshot_id: uuid.UUID) -> SchemaSnapshot | None:
    return await session.get(SchemaSnapshot, snapshot_id)


async def latest_summaries(
    session: AsyncSession, connection_ids: list[uuid.UUID]
) -> dict[uuid.UUID, tuple[int, datetime]]:
    """Each connection's latest snapshot size and time, in one query."""
    if not connection_ids:
        return {}
    rows = (
        await session.execute(
            select(
                SchemaSnapshot.connection_id,
                SchemaSnapshot.object_count,
                SchemaSnapshot.captured_at,
            )
            .where(SchemaSnapshot.connection_id.in_(connection_ids))
            .ext(distinct_on(SchemaSnapshot.connection_id))
            .order_by(
                SchemaSnapshot.connection_id,
                SchemaSnapshot.captured_at.desc(),
                SchemaSnapshot.id.desc(),
            )
        )
    ).all()
    return {r[0]: (int(r[1]), r[2]) for r in rows}


def search(snapshot: SnapshotOut, q: str, limit: int = 100) -> list[SearchHit]:
    """Objects, columns, indexes and foreign keys whose name contains ``q``."""
    needle = q.strip().lower()
    hits: list[SearchHit] = []
    for o in snapshot.objects:
        name = o.qualified
        if needle in o.name.lower():
            hits.append(
                SearchHit(object=name, object_kind=o.kind, match_kind="object", name=o.name)
            )
        for c in o.columns:
            if needle in c.name.lower():
                hits.append(
                    SearchHit(
                        object=name,
                        object_kind=o.kind,
                        match_kind="column",
                        name=c.name,
                        detail=c.type,
                    )
                )
        for ix in o.indexes:
            if needle in ix.name.lower():
                hits.append(
                    SearchHit(
                        object=name,
                        object_kind=o.kind,
                        match_kind="index",
                        name=ix.name,
                        detail=", ".join(ix.columns),
                    )
                )
        for fk in o.foreign_keys:
            label = fk.name or ", ".join(fk.columns)
            if needle in label.lower() or needle in fk.ref_table.lower():
                hits.append(
                    SearchHit(
                        object=name,
                        object_kind=o.kind,
                        match_kind="foreign_key",
                        name=label,
                        detail=f"-> {fk.ref_table}({', '.join(fk.ref_columns)})",
                    )
                )
        if len(hits) >= limit:
            break
    return hits[:limit]


def relationships(snapshot: SnapshotOut) -> Relationships:
    """The ER view: tables and views with their columns, foreign keys as edges."""
    if snapshot.engine not in SQL_ENGINES:
        raise UnprocessableError(
            "The relationship view is for SQL databases; MongoDB and Redis have no foreign keys",
            code="engine_not_supported",
        )
    nodes = [
        RelationshipNode(
            id=o.qualified, name=o.name, kind=o.kind, row_count=o.row_count, columns=o.columns
        )
        for o in snapshot.objects
    ]
    edges = [
        RelationshipEdge(
            name=fk.name,
            source=o.qualified,
            source_columns=fk.columns,
            target=fk.ref_table,
            target_columns=fk.ref_columns,
        )
        for o in snapshot.objects
        for fk in o.foreign_keys
    ]
    return Relationships(snapshot_id=snapshot.id, nodes=nodes, edges=edges)


def find_object(snapshot: SnapshotOut, name: str) -> SchemaObject:
    """The snapshot's object named ``name`` (qualified or bare), or 404."""
    lowered = name.strip().lower()
    for o in snapshot.objects:
        if o.qualified.lower() == lowered or o.name.lower() == lowered:
            return o
    raise NotFoundError(
        f"{name!r} is not in this connection's discovered schema", code="unknown_object"
    )

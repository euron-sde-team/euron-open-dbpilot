"""Schema comparison (AC8): a diff of two snapshots and a generated migration script.

DBPilot never executes the script. ``prepare_run`` hands it to the same confirmed-write path
a person's own write takes (``queries/writes.py``): described statement by statement, held
for a second confirmation, audited before it runs, and run only on the ``from`` connection
by someone allowed to write there.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from dbpilot.audit.service import record
from dbpilot.comparisons.diff import compare
from dbpilot.comparisons.migration import generate
from dbpilot.comparisons.models import SchemaComparison
from dbpilot.comparisons.schemas import ComparisonIn, ComparisonOut, ScriptStatement
from dbpilot.confirmations.models import Confirmation
from dbpilot.connections.access import ConnectionAccess, accessible, visible_ids
from dbpilot.core.container import Container
from dbpilot.core.errors import NotFoundError, UnprocessableError
from dbpilot.core.schemas import decode_cursor, encode_cursor
from dbpilot.projects.access import Principal
from dbpilot.queries import guard, writes
from dbpilot.schema import service as schemas
from dbpilot.schema.models import SchemaSnapshot
from dbpilot.schema.schemas import SchemaObject

SQL_ENGINES = ("postgresql", "mysql", "mssql", "sqlite")


def to_out(c: SchemaComparison, captured: dict[uuid.UUID, datetime] | None = None) -> ComparisonOut:
    captured = captured or {}
    return ComparisonOut(
        id=c.id,
        engine=c.engine,
        from_snapshot_id=c.from_snapshot_id,
        to_snapshot_id=c.to_snapshot_id,
        from_connection_id=c.from_connection_id,
        to_connection_id=c.to_connection_id,
        from_captured_at=captured.get(c.from_snapshot_id),
        to_captured_at=captured.get(c.to_snapshot_id),
        identical=bool((c.diff or {}).get("identical")),
        diff=c.diff or {},
        migration_sql=c.migration_sql,
        statements=[ScriptStatement.model_validate(s) for s in c.statements or []],
        notes=list(c.notes or []),
        destructive=c.destructive,
        runnable=c.runnable,
        runnable_detail=c.runnable_detail,
        created_by_label=c.created_by_label,
        created_at=c.created_at,
    )


async def _snapshot(session: AsyncSession, p: Principal, snapshot_id: uuid.UUID) -> SchemaSnapshot:
    row = await schemas.snapshot_row(session, snapshot_id)
    if row is None or row.project_id != p.project_id:
        raise NotFoundError("Snapshot not found")
    await accessible(session, p, row.connection_id)
    return row


async def create(
    container: Container, session: AsyncSession, p: Principal, data: ComparisonIn
) -> ComparisonOut:
    if data.from_snapshot_id is not None and data.to_snapshot_id is not None:
        before = await _snapshot(session, p, data.from_snapshot_id)
        after = await _snapshot(session, p, data.to_snapshot_id)
    else:
        assert data.from_connection_id is not None and data.to_connection_id is not None
        rows = []
        for cid in (data.from_connection_id, data.to_connection_id):
            conn = (await accessible(session, p, cid)).connection
            snap = await schemas.ensure(container, session, conn, p.actor, org_id=p.org_id)
            row = await schemas.snapshot_row(session, snap.id)
            assert row is not None
            rows.append(row)
        before, after = rows
    if before.engine != after.engine or before.engine not in SQL_ENGINES:
        raise UnprocessableError(
            "Schema comparison works between two snapshots of the same SQL engine",
            code="engine_mismatch",
        )
    diff = compare(
        [SchemaObject.model_validate(o) for o in before.objects],
        [SchemaObject.model_validate(o) for o in after.objects],
    )
    script = generate(diff, before.engine)
    runnable, detail = True, None
    if script.statements:
        try:
            guard.classify(script.sql, before.engine)
        except guard.UnsafeSqlError as exc:
            runnable, detail = False, exc.message
    c = SchemaComparison(
        id=uuid.uuid4(),
        project_id=p.project_id,
        from_snapshot_id=before.id,
        to_snapshot_id=after.id,
        from_connection_id=before.connection_id,
        to_connection_id=after.connection_id,
        engine=before.engine,
        diff=diff.as_dict(),
        migration_sql=script.sql,
        statements=[{"sql": s, "destructive": d} for s, d in script.statements],
        notes=script.notes,
        destructive=script.destructive,
        runnable=runnable,
        runnable_detail=detail,
        statement_count=len(script.statements),
        created_by=p.user_id,
        created_by_label=p.actor.label,
        created_at=datetime.now(UTC),
    )
    session.add(c)
    record(
        session,
        p.actor,
        "schema.compared",
        target_type="schema_comparison",
        target_id=c.id,
        connection_id=before.connection_id,
        to_connection_id=str(after.connection_id),
        statements=len(script.statements),
        destructive=script.destructive,
        identical=diff.identical,
        **p.scope(),
    )
    await session.commit()
    return to_out(c, {before.id: before.captured_at, after.id: after.captured_at})


async def get(session: AsyncSession, p: Principal, comparison_id: uuid.UUID) -> SchemaComparison:
    c = await session.scalar(
        select(SchemaComparison).where(
            SchemaComparison.id == comparison_id, SchemaComparison.project_id == p.project_id
        )
    )
    if c is None:
        raise NotFoundError("Comparison not found")
    # Both sides must be visible (AC9): otherwise it does not exist for this caller.
    for cid in (c.from_connection_id, c.to_connection_id):
        try:
            await accessible(session, p, cid)
        except NotFoundError:
            raise NotFoundError("Comparison not found") from None
    return c


async def list_comparisons(
    session: AsyncSession, p: Principal, cursor: str | None, limit: int
) -> tuple[list[SchemaComparison], str | None]:
    ids = await visible_ids(session, p)
    stmt = select(SchemaComparison).where(SchemaComparison.project_id == p.project_id)
    if ids is not None:
        stmt = stmt.where(
            SchemaComparison.from_connection_id.in_(ids), SchemaComparison.to_connection_id.in_(ids)
        )
    after = decode_cursor(cursor)
    if after:
        at, _, last = after.partition("|")
        try:
            at_dt, last_id = datetime.fromisoformat(at), uuid.UUID(last)
            stmt = stmt.where(
                (SchemaComparison.created_at < at_dt)
                | ((SchemaComparison.created_at == at_dt) & (SchemaComparison.id < last_id))
            )
        except ValueError:
            pass
    rows = list(
        (
            await session.scalars(
                stmt.order_by(SchemaComparison.created_at.desc(), SchemaComparison.id.desc()).limit(
                    limit + 1
                )
            )
        ).all()
    )
    nxt = None
    if len(rows) > limit:
        last_c = rows[limit - 1]
        nxt = encode_cursor(f"{last_c.created_at.isoformat()}|{last_c.id}")
    return rows[:limit], nxt


async def prepare_run(
    container: Container,
    session: AsyncSession,
    p: Principal,
    c: SchemaComparison,
    note: str,
) -> tuple[Confirmation, str, ConnectionAccess]:
    """Hand the script to the confirmed-write path, for the ``from`` connection only."""
    if not c.statements:
        raise UnprocessableError(
            "The two schemas already match: there is nothing to run", code="nothing_to_run"
        )
    if not c.runnable:
        raise UnprocessableError(
            f"The script cannot run as generated: {c.runnable_detail}", code="script_not_runnable"
        )
    access = await accessible(session, p, c.from_connection_id)
    confirmation, token = await writes.prepare(
        container,
        session,
        p,
        access,
        c.migration_sql,
        note=note or f"migration from comparison {c.id}",
        kind="migration",
        comparison_id=c.id,
        skip_ai_check=True,
    )
    return confirmation, token, access


async def referenced_snapshot_ids(
    session: AsyncSession, snapshot_ids: list[uuid.UUID]
) -> set[uuid.UUID]:
    if not snapshot_ids:
        return set()
    rows = (
        await session.execute(
            select(SchemaComparison.from_snapshot_id, SchemaComparison.to_snapshot_id).where(
                or_(
                    SchemaComparison.from_snapshot_id.in_(snapshot_ids),
                    SchemaComparison.to_snapshot_id.in_(snapshot_ids),
                )
            )
        )
    ).all()
    return {i for row in rows for i in row if i in set(snapshot_ids)}

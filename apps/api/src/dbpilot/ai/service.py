"""The AI database assistant: natural language to SQL, explanations, index suggestions and
error diagnosis, all against the connection's real discovered schema.

The safety contract (AC4, AC6):

* The model only ever sees the schema the live discovery returned (``schema.service``, the
  same call the explorer uses), and a generated statement that names a table or column the
  discovery did not return is refused as fabricated.
* Every generated statement goes through the SQL guard, unconditionally. Anything other
  than exactly one read-only query is refused and can never run: no flag, no approval, no
  "run anyway". Running an accepted generation sends its stored SQL through the normal read
  path (guard again, read-only session, limits); running a refused one is refused and
  audited. The confirmed-write path refuses any statement matching one the assistant
  generated (``queries/writes.py``).
* Every output is kept in ``ai_generations``, accepted or refused, with an audit entry.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dbpilot.ai import advice, identifiers, prompt
from dbpilot.ai.describe import analyze, describe
from dbpilot.ai.models import AiGeneration
from dbpilot.ai.providers import Completion, Message, ProviderError, from_settings
from dbpilot.ai.schemas import AnalysisOut, GenerationOut, GuardOut
from dbpilot.audit.service import record
from dbpilot.connections.access import ConnectionAccess, accessible, visible_clause, visible_ids
from dbpilot.connections.models import Connection
from dbpilot.core import metrics
from dbpilot.core.container import Container
from dbpilot.core.errors import ConflictError, NotFoundError, UnprocessableError, UpstreamError
from dbpilot.core.schemas import decode_cursor, encode_cursor
from dbpilot.projects.access import Principal
from dbpilot.queries import guard
from dbpilot.queries import service as queries
from dbpilot.schema import service as schemas
from dbpilot.schema.schemas import SchemaObject, SnapshotOut

SQL_ENGINES = ("postgresql", "mysql", "mssql", "sqlite")
MAX_CONTEXT_CHARS = 60_000


def _sql_only(conn: Connection) -> None:
    if conn.kind not in SQL_ENGINES:
        raise UnprocessableError(
            f"The AI assistant writes and explains SQL; {conn.kind} connections use their own "
            "read-only query surface instead",
            code="engine_not_supported",
        )


def _tables(snapshot: SnapshotOut) -> list[SchemaObject]:
    return [o for o in snapshot.objects if o.kind in ("table", "view")]


def to_out(g: AiGeneration) -> GenerationOut:
    unknown = g.unknown_identifiers or {}
    return GenerationOut(
        id=g.id,
        kind=g.kind,
        status=g.status,
        connection_id=g.connection_id,
        prompt=g.prompt,
        sql=g.generated_sql,
        explanation=g.explanation,
        guard=GuardOut(passed=g.status == "accepted", code=g.guard_code, reason=g.guard_reason),
        unknown_tables=list(unknown.get("tables", [])),
        unknown_columns=list(unknown.get("columns", [])),
        tables=list(g.tables or []),
        executable=g.kind == "sql" and g.status == "accepted" and bool(g.generated_sql),
        output=g.output or {},
        provider=g.provider,
        model=g.model,
        input_tokens=g.input_tokens,
        output_tokens=g.output_tokens,
        latency_ms=g.latency_ms,
        schema_snapshot_id=g.schema_snapshot_id,
        actor_label=g.actor_label,
        created_at=g.created_at,
    )


def _new(p: Principal, conn: Connection | None, kind: str, prompt_text: str) -> AiGeneration:
    return AiGeneration(
        id=uuid.uuid4(),
        project_id=p.project_id,
        connection_id=conn.id if conn else None,
        kind=kind,
        status="failed",
        prompt=prompt_text,
        actor_id=p.user_id,
        actor_label=p.actor.label,
        created_at=datetime.now(UTC),
    )


async def _complete(
    container: Container, messages: tuple[Message, ...], max_tokens: int
) -> tuple[Completion, str]:
    """One model call; returns the answer and the provider's name."""
    provider = from_settings(container.settings, container.http)
    started = time.perf_counter()
    try:
        return await provider.complete(messages, max_tokens=max_tokens), provider.name
    finally:
        metrics.AI_MODEL_SECONDS.labels(provider.name).observe(time.perf_counter() - started)


def _apply(g: AiGeneration, c: Completion, provider: str) -> None:
    g.provider = provider
    g.model = c.model
    g.input_tokens, g.output_tokens, g.latency_ms = c.input_tokens, c.output_tokens, c.latency_ms


async def _save(
    session: AsyncSession, p: Principal, g: AiGeneration, action: str, **details: Any
) -> AiGeneration:
    session.add(g)
    record(
        session,
        p.actor,
        action,
        target_type="ai_generation",
        target_id=g.id,
        connection_id=g.connection_id,
        kind=g.kind,
        status=g.status,
        prompt=g.prompt,
        sql=g.generated_sql,
        guard_code=g.guard_code,
        guard_reason=g.guard_reason,
        model=g.model,
        **details,
        **p.scope(),
    )
    await session.commit()
    metrics.AI_GENERATIONS.labels(g.kind, g.status).inc()
    return g


async def generate_sql(
    container: Container,
    session: AsyncSession,
    p: Principal,
    access: ConnectionAccess,
    question: str,
) -> AiGeneration:
    conn = access.connection
    _sql_only(conn)
    snapshot = await schemas.ensure(container, session, conn, p.actor, org_id=p.org_id)
    tables = _tables(snapshot)
    messages = prompt.generate_messages(
        dialect=conn.kind,
        tables=tables,
        question=question,
        max_schema_chars=container.settings.prompt_schema_chars,
        connection_note=conn.description,
    )
    g = _new(p, conn, "sql", question)
    g.schema_snapshot_id = snapshot.id
    g.schema_context = messages[0].content[:MAX_CONTEXT_CHARS]
    try:
        completion, provider = await _complete(container, messages, 1500)
    except ProviderError as exc:
        g.status, g.guard_code, g.guard_reason = "failed", exc.code, exc.message
        await _save(session, p, g, "ai.failed")
        raise UpstreamError(exc.message, code=exc.code) from exc
    _apply(g, completion, provider)
    answer = prompt.parse_answer(completion.text)
    g.explanation = answer.explanation
    g.generated_sql = answer.sql
    if answer.sql is None:
        g.status = "no_sql"
        return await _save(session, p, g, "ai.no_sql")
    g.sql_fingerprint = guard.fingerprint(answer.sql, conn.kind)
    g.statement_fingerprints = guard.statement_fingerprints(answer.sql, conn.kind)
    try:
        verdict = guard.check(answer.sql, conn.kind)
    except guard.UnsafeSqlError as exc:
        # AC4: anything but one read-only query is refused here, with no way around it.
        g.status, g.guard_code, g.guard_reason = "refused", exc.code, exc.message
        metrics.GUARD_REFUSALS.labels("ai", exc.code).inc()
        return await _save(session, p, g, "ai.refused")
    report = identifiers.check_identifiers(verdict.tree, tables)  # type: ignore[arg-type]
    if not report.ok:
        # AC6: a name the discovery did not return is fabricated; it never runs.
        g.status, g.guard_code, g.guard_reason = "refused", "unknown_identifier", report.message()
        g.unknown_identifiers = {"tables": report.unknown_tables, "columns": report.unknown_columns}
        metrics.GUARD_REFUSALS.labels("ai", "unknown_identifier").inc()
        return await _save(session, p, g, "ai.refused")
    g.status = "accepted"
    g.generated_sql = verdict.sql
    g.tables = verdict.tables
    if not g.explanation:
        g.explanation = describe(verdict.sql, conn.kind)
    return await _save(session, p, g, "ai.generated")


async def get(session: AsyncSession, p: Principal, generation_id: uuid.UUID) -> AiGeneration:
    ids = await visible_ids(session, p)
    clause = visible_clause(AiGeneration.connection_id, ids)
    g = await session.scalar(
        select(AiGeneration).where(
            AiGeneration.id == generation_id,
            AiGeneration.project_id == p.project_id,
            clause | AiGeneration.connection_id.is_(None),
        )
    )
    if g is None:
        raise NotFoundError("Generation not found")
    return g


async def run_generation(
    container: Container, session: AsyncSession, p: Principal, generation_id: uuid.UUID
) -> queries.Outcome:
    """Run an accepted generation's stored SQL through the normal read path (the guard
    checks it again there). A refused generation never runs."""
    g = await get(session, p, generation_id)
    if g.connection_id is None:
        raise NotFoundError("Generation not found")
    access = await accessible(session, p, g.connection_id)
    if g.kind != "sql" or g.status != "accepted" or not g.generated_sql:
        record(
            session,
            p.actor,
            "ai.run_refused",
            target_type="ai_generation",
            target_id=g.id,
            connection_id=g.connection_id,
            status=g.status,
            sql=g.generated_sql,
            **p.scope(),
        )
        await session.commit()
        metrics.GUARD_REFUSALS.labels("ai", "refused_generation").inc()
        raise ConflictError(
            "This AI-generated statement was refused and can never run: DBPilot only runs an "
            "AI-generated statement when it is a single read-only query naming real tables",
            code="ai_statement_refused",
        )
    return await queries.run_sql(
        container, session, p, access, g.generated_sql, source="ai", ai_generation_id=g.id
    )


async def explain(
    container: Container,
    session: AsyncSession,
    p: Principal,
    sql: str,
    dialect: str,
    conn: Connection | None,
    *,
    use_model: bool,
) -> tuple[AiGeneration, AnalysisOut, str]:
    """Explain any statement in plain language. It is parsed, never executed."""
    if dialect not in SQL_ENGINES:
        raise UnprocessableError("Explanations are for SQL statements", code="engine_not_supported")
    facts = analyze(sql, dialect)
    g = _new(p, conn, "explain", sql)
    g.explanation, source = describe(sql, dialect), "syntax"
    if use_model and container.settings.ai_provider != "none":
        try:
            completion, provider = await _complete(
                container, prompt.explain_messages(dialect=dialect, sql=sql), 800
            )
        except ProviderError as exc:
            g.output = {"model_error": exc.message}
        else:
            _apply(g, completion, provider)
            text = prompt.parse_field(completion.text, "explanation")
            if text:
                g.explanation, source = text, "model"
    g.status = "answered"
    g.output = {**(g.output or {}), "source": source}
    await _save(session, p, g, "ai.explained")
    return g, AnalysisOut(**asdict(facts)), source


async def recent_statements(
    session: AsyncSession, connection_id: uuid.UUID, limit: int = 200
) -> list[str]:
    from dbpilot.queries.models import QueryRun

    return list(
        (
            await session.scalars(
                select(QueryRun.statement)
                .where(QueryRun.connection_id == connection_id, QueryRun.status == "succeeded")
                .order_by(QueryRun.created_at.desc())
                .limit(limit)
            )
        ).all()
    )


async def index_suggestions(
    container: Container, session: AsyncSession, p: Principal, access: ConnectionAccess
) -> tuple[AiGeneration, SnapshotOut, list[advice.IndexSuggestion], int]:
    conn = access.connection
    _sql_only(conn)
    snapshot = await schemas.ensure(container, session, conn, p.actor, org_id=p.org_id)
    history = await recent_statements(session, conn.id)
    found = advice.index_suggestions(_tables(snapshot), conn.kind, history)
    g = _new(p, conn, "index_suggestions", f"index suggestions for {conn.name}")
    g.status = "answered"
    g.schema_snapshot_id = snapshot.id
    g.provider, g.model = "rules", "schema-rules"
    g.output = {"suggestions": [asdict(s) for s in found], "analyzed_queries": len(history)}
    await _save(session, p, g, "ai.index_suggestions", suggestions=len(found))
    return g, snapshot, found, len(history)


async def diagnose(
    container: Container,
    session: AsyncSession,
    p: Principal,
    access: ConnectionAccess,
    sql: str,
    error: str,
) -> tuple[AiGeneration, advice.Diagnosis, str | None]:
    conn = access.connection
    _sql_only(conn)
    snapshot = await schemas.ensure(container, session, conn, p.actor, org_id=p.org_id)
    tables = _tables(snapshot)
    found = advice.diagnose(sql, error, tables)
    g = _new(p, conn, "diagnose", sql)
    g.status = "answered"
    g.schema_snapshot_id = snapshot.id
    g.explanation = found.explanation
    narrative: str | None = None
    if container.settings.ai_provider != "none":
        messages = prompt.diagnose_messages(
            dialect=conn.kind,
            sql=sql,
            error=error,
            tables=tables,
            max_schema_chars=container.settings.prompt_schema_chars,
        )
        try:
            completion, provider = await _complete(container, messages, 800)
        except ProviderError as exc:
            g.output = {"model_error": exc.message}
        else:
            _apply(g, completion, provider)
            narrative = prompt.parse_field(completion.text, "diagnosis")
    g.output = {**(g.output or {}), **asdict(found), "model_explanation": narrative, "error": error}
    await _save(session, p, g, "ai.diagnosed", category=found.category)
    return g, found, narrative


async def matches_generated(session: AsyncSession, project_id: uuid.UUID, prints: set[str]) -> bool:
    """Whether any of these statement fingerprints was generated by the assistant here."""
    if not prints:
        return False
    hit = await session.scalar(
        select(AiGeneration.id)
        .where(
            AiGeneration.project_id == project_id,
            AiGeneration.statement_fingerprints.overlap(sorted(prints)),
        )
        .limit(1)
    )
    return hit is not None


async def list_generations(
    session: AsyncSession,
    p: Principal,
    *,
    connection_id: uuid.UUID | None,
    kind: str | None,
    status: str | None,
    cursor: str | None,
    limit: int,
) -> tuple[list[AiGeneration], str | None]:
    ids = await visible_ids(session, p)
    stmt = select(AiGeneration).where(
        AiGeneration.project_id == p.project_id,
        visible_clause(AiGeneration.connection_id, ids) | AiGeneration.connection_id.is_(None),
    )
    if connection_id:
        stmt = stmt.where(AiGeneration.connection_id == connection_id)
    if kind:
        stmt = stmt.where(AiGeneration.kind == kind)
    if status:
        stmt = stmt.where(AiGeneration.status == status)
    after = decode_cursor(cursor)
    if after:
        at, _, last = after.partition("|")
        try:
            at_dt, last_id = datetime.fromisoformat(at), uuid.UUID(last)
            stmt = stmt.where(
                (AiGeneration.created_at < at_dt)
                | ((AiGeneration.created_at == at_dt) & (AiGeneration.id < last_id))
            )
        except ValueError:
            pass
    rows = list(
        (
            await session.scalars(
                stmt.order_by(AiGeneration.created_at.desc(), AiGeneration.id.desc()).limit(
                    limit + 1
                )
            )
        ).all()
    )
    nxt = None
    if len(rows) > limit:
        last_row = rows[limit - 1]
        nxt = encode_cursor(f"{last_row.created_at.isoformat()}|{last_row.id}")
    return rows[:limit], nxt

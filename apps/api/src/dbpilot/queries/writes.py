"""The human write path (AC5), separate from the read path and from the AI assistant.

A person deliberately marks a statement (or a script) as a write. ``prepare`` checks they
may write to the connection, classifies every statement (``guard.classify``) so the
confirmation can say exactly what will run, and holds it as a pending confirmation: nothing
touches the database. ``execute`` runs only after ``confirmations.service.begin`` committed
the ``write.confirmed`` audit entry, on a write-capable connection the gateway opens for
that confirmation alone.

AI-generated statements never come through here (AC4): a statement whose normalized form
matches anything the AI assistant generated in this project is refused, so "copy the
refused AI statement into the write box" does not work either. The person writes their
own statement, and owns it.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from sqlalchemy.engine import Connection as SaConnection
from sqlalchemy.ext.asyncio import AsyncSession

from dbpilot.audit.service import record
from dbpilot.confirmations import service as confirmations
from dbpilot.confirmations.models import Confirmation
from dbpilot.connections import gateway
from dbpilot.connections.access import ConnectionAccess, require_write
from dbpilot.connections.connectors.sql import StatementOutcome, run_statements
from dbpilot.connections.models import Connection
from dbpilot.core import metrics
from dbpilot.core.container import Container
from dbpilot.core.errors import UnprocessableError
from dbpilot.core.security import sha256_text
from dbpilot.projects.access import Principal
from dbpilot.queries import guard
from dbpilot.queries.models import QueryRun
from dbpilot.queries.service import apply_error, new_run

log = logging.getLogger("dbpilot.writes")
WRITABLE_ENGINES = ("postgresql", "mysql", "mssql", "sqlite")
DDL_KINDS = ("create", "drop", "alter", "truncate")


def digest_of(statements: list[guard.StatementInfo]) -> str:
    return sha256_text(json.dumps([s.sql for s in statements]))


def warnings_for(conn: Connection, statements: list[guard.StatementInfo]) -> list[str]:
    out = [f"Statement {s.index + 1}: {s.summary}" for s in statements if s.destructive]
    if (
        conn.kind == "mysql"
        and any(s.kind in DDL_KINDS for s in statements)
        and len(statements) > 1
    ):
        out.append(
            "MySQL commits each schema change (DDL) on its own: if a later statement fails, "
            "earlier ones stay applied"
        )
    if conn.kind == "sqlite":
        out.append("SQLite writes go to a copy that replaces the stored file only on success")
    return out


async def _refuse(
    session: AsyncSession,
    p: Principal,
    conn: Connection,
    kind: str,
    sql: str,
    code: str,
    message: str,
) -> None:
    record(
        session,
        p.actor,
        f"{kind}.refused",
        target_type="connection",
        target_id=conn.id,
        connection_id=conn.id,
        connection=conn.name,
        engine=conn.kind,
        statement=sql,
        code=code,
        reason=message,
        **p.scope(),
    )
    await session.commit()
    metrics.GUARD_REFUSALS.labels(kind, code).inc()
    metrics.CONFIRMATIONS.labels(kind, "rejected").inc()


async def prepare(
    container: Container,
    session: AsyncSession,
    p: Principal,
    access: ConnectionAccess,
    sql: str,
    *,
    note: str = "",
    kind: str = "write",
    comparison_id: Any = None,
    skip_ai_check: bool = False,
) -> tuple[Confirmation, str]:
    """Describe the statements and hold them for confirmation. Nothing runs."""
    from dbpilot.ai import service as ai

    require_write(access)
    conn = access.connection
    if conn.kind not in WRITABLE_ENGINES:
        raise UnprocessableError(
            f"Writes to {conn.kind} are not offered in DBPilot 0.1.0: its surface is read-only",
            code="engine_not_supported",
        )
    try:
        statements = guard.classify(sql, conn.kind)
    except guard.UnsafeSqlError as exc:
        await _refuse(session, p, conn, kind, sql, exc.code, exc.message)
        raise UnprocessableError(exc.message, code=exc.code) from exc
    if not skip_ai_check:
        prints = set(guard.statement_fingerprints(sql, conn.kind))
        prints |= {fp for s in statements if (fp := guard.fingerprint(s.sql, conn.kind))}
        if await ai.matches_generated(session, p.project_id, prints):
            message = (
                "This statement was written by the AI assistant. AI-generated statements "
                "never run as writes in DBPilot, with or without confirmation: write the "
                "statement yourself if you intend this change"
            )
            await _refuse(session, p, conn, kind, sql, "ai_generated_statement", message)
            raise UnprocessableError(message, code="ai_generated_statement")
    payload = {
        "sql": sql,
        "note": note,
        "statements": [asdict(s) for s in statements],
        "warnings": warnings_for(conn, statements),
    }
    summary = "; ".join(s.summary for s in statements)[:2000]
    return await confirmations.create(
        session,
        p,
        conn,
        kind=kind,
        payload=payload,
        digest=digest_of(statements),
        summary=summary,
        ttl_s=container.settings.confirmation_ttl_s,
        audit_details={"statements": [s.sql for s in statements], "note": note},
        comparison_id=comparison_id,
    )


async def execute(
    container: Container,
    session: AsyncSession,
    p: Principal,
    c: Confirmation,
    conn: Connection,
) -> QueryRun:
    """Run a confirmed write or migration (``c`` is committed as ``executing``)."""
    from dbpilot.schema import service as schemas

    payload = c.payload or {}
    infos = payload.get("statements", [])
    statements = [str(s["sql"]) for s in infos]
    source = "migration" if c.kind == "migration" else "write"
    run = new_run(
        conn,
        p.actor,
        source=source,
        statement=";\n".join(statements),
        mode="write",
        confirmation_id=c.id,
    )
    outcomes: list[StatementOutcome] = []

    def work(handle: SaConnection, stopped: Callable[[], bool]) -> list[StatementOutcome]:
        return run_statements(handle, statements, stopped)

    def on_done(
        result: list[StatementOutcome] | None, error: BaseException | None
    ) -> dict[str, Any]:
        if result is not None:
            outcomes.extend(result)
            run.status = "succeeded"
            run.rows_affected = sum(o.rows_affected for o in result)
            run.duration_ms = round(sum(o.duration_ms for o in result), 1)
        else:
            apply_error(run, conn, error)
        session.add(run)
        return {
            "query_id": str(run.id),
            "status": run.status,
            "rows_affected": run.rows_affected,
            "per_statement": [asdict(o) for o in outcomes],
        }

    try:
        await gateway.write(
            container,
            session,
            conn,
            p.actor,
            confirmation_id=c.id,
            action=f"{c.kind}.executed",
            work=work,
            org_id=p.org_id,
            details={"statements": statements, "digest": c.digest},
            on_done=on_done,
            # Each statement gets the connection's time limit; the whole write that many
            # times over, plus room to connect and commit.
            deadline_s=conn.timeout_s * max(1, len(statements)) + 30,
        )
    except Exception as exc:  # recorded on the run, the audit entry and the confirmation
        log.warning(
            "confirmed write failed",
            extra={"confirmation": str(c.id), "error": gateway.error_text(exc)},
        )
    if run.status == "succeeded" and any(s.get("kind") in DDL_KINDS for s in infos):
        await schemas.invalidate(session, conn)
        await session.commit()
    metrics.QUERIES.labels(conn.kind, "write", source, run.status).inc()
    if run.status == "succeeded":
        metrics.QUERY_SECONDS.labels(conn.kind, "write").observe(run.duration_ms / 1000)
    await confirmations.finish(
        session,
        c,
        ok=run.status == "succeeded",
        result={
            "query_id": str(run.id),
            "rows_affected": run.rows_affected,
            "statements": [asdict(o) for o in outcomes],
        },
        error=run.error,
    )
    return run

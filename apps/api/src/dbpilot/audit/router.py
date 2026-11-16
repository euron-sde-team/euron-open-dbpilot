from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query

from dbpilot.audit import service
from dbpilot.audit.models import AuditLog
from dbpilot.auth.dependencies import CurrentUser
from dbpilot.core.deps import SessionDep
from dbpilot.core.schemas import ApiModel, Page, decode_cursor, encode_cursor
from dbpilot.orgs import service as orgs
from dbpilot.projects.access import Admin

router = APIRouter(tags=["audit"])


class AuditEntryOut(ApiModel):
    id: int
    at: datetime
    actor_id: uuid.UUID | None
    actor_email: str | None
    actor_role: str
    action: str
    target_type: str | None
    target_id: str | None
    connection_id: uuid.UUID | None
    details: dict[str, Any]
    ip: str | None
    org_id: uuid.UUID | None
    project_id: uuid.UUID | None


def _page(rows: list[AuditLog], limit: int) -> Page[AuditEntryOut]:
    nxt = encode_cursor(rows[limit - 1].id) if len(rows) > limit else None
    return Page[AuditEntryOut](
        items=[AuditEntryOut.model_validate(r) for r in rows[:limit]], next_cursor=nxt
    )


def _after(cursor: str | None) -> int | None:
    raw = decode_cursor(cursor)
    return int(raw) if raw and raw.isdigit() else None


@router.get(
    "/api/v1/projects/{project_id}/audit",
    response_model=Page[AuditEntryOut],
    summary="The project's append-only audit log, newest first (project admins)",
)
async def project_audit(
    p: Admin,
    session: SessionDep,
    action: str | None = Query(None, max_length=64, description="Prefix, e.g. query. or write."),
    connection_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> Page[AuditEntryOut]:
    rows = await service.list_entries(
        session,
        limit=limit,
        after_id=_after(cursor),
        action=action,
        project_id=p.project_id,
        connection_id=connection_id,
        actor_id=actor_id,
    )
    return _page(rows, limit)


@router.get(
    "/api/v1/orgs/{org_id}/audit",
    response_model=Page[AuditEntryOut],
    summary="The organization's audit log across its projects (owners, admins)",
)
async def org_audit(
    org_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    action: str | None = Query(None, max_length=64),
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> Page[AuditEntryOut]:
    await orgs.get_for(session, user, org_id, "admin")
    rows = await service.list_entries(
        session, limit=limit, after_id=_after(cursor), action=action, org_id=org_id
    )
    return _page(rows, limit)

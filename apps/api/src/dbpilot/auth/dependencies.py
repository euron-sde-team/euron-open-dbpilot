"""Authentication: a dashboard session, sent as ``Authorization: Bearer <token>`` (scripts,
SDKs) or as the HttpOnly ``dbp_session`` cookie (the dashboard).

Cookie-authenticated mutations must carry ``X-DBP-CSRF: 1``. Browsers cannot add a custom
header to a cross-site request without a CORS preflight, which the allowlist refuses, so
this blocks CSRF without server-side token state.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from dbpilot.audit.service import Actor
from dbpilot.auth.models import RevokedSession
from dbpilot.core.deps import ContainerDep, SessionDep, client_ip
from dbpilot.core.errors import AuthenticationError, PermissionDeniedError
from dbpilot.core.middleware import SESSION_COOKIE
from dbpilot.core.security import SessionClaims, decode_session
from dbpilot.orgs.models import User

CSRF_HEADER = "x-dbp-csrf"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


async def is_revoked(session: AsyncSession, claims: SessionClaims) -> bool:
    if not claims.jti:
        return False
    return await session.get(RevokedSession, claims.jti) is not None


async def revoke(session: AsyncSession, claims: SessionClaims) -> None:
    """Record a signed-out session and prune rows whose tokens have expired anyway."""
    if not claims.jti or claims.expires_at is None:
        return
    now = datetime.now(UTC)
    await session.execute(delete(RevokedSession).where(RevokedSession.expires_at < now))
    if claims.expires_at > now:
        await session.execute(
            insert(RevokedSession)
            .values(jti=claims.jti, expires_at=claims.expires_at)
            .on_conflict_do_nothing()
        )


def bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    return header[7:].strip() or None if header.lower().startswith("bearer ") else None


def session_token(request: Request) -> tuple[str | None, bool]:
    """The session token and whether it came from the cookie."""
    token = bearer(request)
    if token:
        return token, False
    cookie = request.cookies.get(SESSION_COOKIE)
    return cookie, cookie is not None


async def current_user(request: Request, container: ContainerDep, session: SessionDep) -> User:
    # An explicit Authorization header wins: browsers never attach it cross-site, so it
    # needs no CSRF check. The cookie is the fallback for the dashboard.
    token, via_cookie = session_token(request)
    if not token:
        raise AuthenticationError("Sign in to continue", code="not_signed_in")
    claims = decode_session(token, container.settings.session_secret)
    if claims is None or await is_revoked(session, claims):
        raise AuthenticationError("Your session expired. Sign in again", code="session_expired")
    user = await session.get(User, claims.user_id)
    if user is None or user.disabled or user.session_version != claims.session_version:
        raise AuthenticationError("Your session expired. Sign in again", code="session_expired")
    if (
        via_cookie
        and request.method not in _SAFE_METHODS
        and request.headers.get(CSRF_HEADER) != "1"
    ):
        raise PermissionDeniedError(
            "Missing the X-DBP-CSRF header on a cookie-authenticated change", code="csrf_required"
        )
    request.state.actor = Actor(user.id, user.email, user.role, client_ip(request))
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def actor_of(request: Request) -> Actor:
    actor: Actor = request.state.actor
    return actor


async def instance_admin(request: Request, user: CurrentUser) -> Actor:
    if user.role != "admin":
        raise PermissionDeniedError(
            "This needs an instance administrator", code="insufficient_role"
        )
    return actor_of(request)


InstanceAdmin = Annotated[Actor, Depends(instance_admin)]

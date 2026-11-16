"""Sign-in with lockout, and password changes that end every other session."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from dbpilot.audit.service import Actor, record
from dbpilot.core.errors import AppError, AuthenticationError
from dbpilot.core.security import hash_password, password_problems, verify_password
from dbpilot.orgs.models import User
from dbpilot.orgs.service import user_by_email

MAX_FAILURES = 5
LOCKOUT = timedelta(minutes=15)


async def authenticate(session: AsyncSession, email: str, password: str, ip: str | None) -> User:
    user = await user_by_email(session, email)
    now = datetime.now(UTC)
    if user is not None and user.locked_until and user.locked_until > now:
        raise AuthenticationError(
            "Too many failed attempts. Try again in a few minutes", code="account_locked"
        )
    # Argon2 is deliberately slow: run it off the event loop.
    ok = await asyncio.to_thread(verify_password, password, user.password_hash if user else None)
    if not ok or user is None:
        if user is not None:
            await _record_failure(session, user, now, ip)
        raise AuthenticationError("Email or password is incorrect", code="invalid_credentials")
    if user.disabled:
        raise AuthenticationError("This account is disabled", code="account_disabled")
    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = now
    record(
        session,
        Actor(user.id, user.email, user.role, ip),
        "auth.login",
        target_type="user",
        target_id=user.id,
    )
    await session.commit()
    return user


async def _record_failure(session: AsyncSession, user: User, now: datetime, ip: str | None) -> None:
    """Count a failure atomically in SQL, so parallel attempts cannot share one count."""
    failures = await session.scalar(
        update(User)
        .where(User.id == user.id)
        .values(failed_logins=User.failed_logins + 1)
        .returning(User.failed_logins)
    )
    locked = failures is not None and failures >= MAX_FAILURES
    if locked:
        await session.execute(
            update(User)
            .where(User.id == user.id)
            .values(locked_until=now + LOCKOUT, failed_logins=0)
        )
    record(
        session,
        Actor(user.id, user.email, user.role, ip),
        "auth.login_failed",
        target_type="user",
        target_id=user.id,
        locked=locked,
    )
    await session.commit()


async def change_password(
    session: AsyncSession, user: User, current: str, new: str, actor: Actor
) -> User:
    if not await asyncio.to_thread(verify_password, current, user.password_hash):
        raise AuthenticationError("Current password is incorrect", code="invalid_credentials")
    if problems := password_problems(new):
        raise AppError(
            "Password needs " + ", ".join(problems),
            code="weak_password",
            details={"problems": problems},
        )
    user.password_hash = await asyncio.to_thread(hash_password, new)
    user.session_version += 1
    record(session, actor, "auth.password_changed", target_type="user", target_id=user.id)
    await session.commit()
    return user

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from redis.exceptions import RedisError

from dbpilot.audit.service import Actor, record
from dbpilot.auth import service
from dbpilot.auth.dependencies import CurrentUser, actor_of, current_user, revoke, session_token
from dbpilot.auth.schemas import LoginRequest, PasswordChange, SessionOut, SignInOut
from dbpilot.core import metrics
from dbpilot.core.deps import ContainerDep, SessionDep, client_ip
from dbpilot.core.errors import AuthenticationError, RateLimitedError
from dbpilot.core.limiter import LimitDecision
from dbpilot.core.middleware import SESSION_COOKIE
from dbpilot.core.schemas import Ack
from dbpilot.core.security import decode_session, issue_session
from dbpilot.orgs.models import User
from dbpilot.orgs.schemas import UserOut

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _sign_in(response: Response, container: ContainerDep, user: User) -> SignInOut:
    settings = container.settings
    token = issue_session(
        user.id,
        user.role,
        user.session_version,
        settings.session_secret,
        settings.session_ttl_hours,
    )
    ttl = settings.session_ttl_hours * 3600
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=ttl,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )
    return SignInOut(user=UserOut.model_validate(user), session_token=token, expires_in_s=ttl)


@router.post("/login", response_model=SignInOut, summary="Sign in")
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    container: ContainerDep,
    session: SessionDep,
) -> SignInOut:
    ip = client_ip(request)
    try:
        decision = await container.kv.hit(f"login:{ip}", container.settings.login_rpm_per_ip)
    except (RedisError, OSError):
        # Per-IP throttling is a convenience; the per-account lockout lives in Postgres and
        # still applies, so sign-in keeps working while Redis is down.
        metrics.DEPENDENCY_ERRORS.labels("cache", "login_limit").inc()
        decision = LimitDecision(allowed=True)
    if not decision.allowed:
        raise RateLimitedError(
            "Too many sign-in attempts from this address. Wait a minute and try again",
            headers={"retry-after": str(decision.retry_after_s)},
        )
    user = await service.authenticate(session, body.email, body.password, ip)
    return _sign_in(response, container, user)


@router.post("/logout", response_model=Ack, summary="Sign out (the token is refused from now on)")
async def logout(
    request: Request, response: Response, container: ContainerDep, session: SessionDep
) -> Ack:
    token, _ = session_token(request)
    claims = decode_session(token, container.settings.session_secret) if token else None
    if claims is not None:
        await revoke(session, claims)
        user = await session.get(User, claims.user_id)
        if user is not None:
            actor = Actor(user.id, user.email, user.role, client_ip(request))
            record(session, actor, "auth.logout", target_type="user", target_id=user.id)
        await session.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return Ack(message="Signed out")


@router.get("/session", response_model=SessionOut, summary="Who is signed in, if anyone")
async def current_session(
    request: Request, container: ContainerDep, session: SessionDep
) -> SessionOut:
    """A signed-out visitor is a normal answer here (200, no user); ``/me`` is strict."""
    if not session_token(request)[0]:
        return SessionOut(user=None)
    try:
        user = await current_user(request, container, session)
    except AuthenticationError:
        return SessionOut(user=None)
    return SessionOut(user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut, summary="The signed-in user")
async def me(user: CurrentUser) -> User:
    return user


@router.post("/password", response_model=SignInOut, summary="Change your password")
async def password(
    body: PasswordChange,
    request: Request,
    response: Response,
    user: CurrentUser,
    container: ContainerDep,
    session: SessionDep,
) -> SignInOut:
    user = await service.change_password(
        session, user, body.current_password, body.new_password, actor_of(request)
    )
    return _sign_in(response, container, user)

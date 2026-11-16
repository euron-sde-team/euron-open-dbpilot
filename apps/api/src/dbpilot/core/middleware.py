"""Pure ASGI middleware (no BaseHTTPMiddleware: it buffers streams and adds latency)."""

from __future__ import annotations

import json
import logging
import re
import secrets
import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from dbpilot.core import metrics
from dbpilot.core.config import Settings
from dbpilot.core.logs import request_id_var
from dbpilot.core.security import decode_session

log = logging.getLogger("dbpilot.http")

SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
]
_QUIET = {"/healthz", "/readyz", "/metrics"}


def _route_template(scope: Scope) -> str:
    """The matched route's path template (bounded label values), never the raw path."""
    route = scope.get("route")
    path = getattr(route, "path", None)
    return str(path) if path else "unmatched"


class RequestContextMiddleware:
    """Assigns a request id, adds security headers, marks API responses ``no-store`` (they
    carry database contents), records HTTP metrics and logs one line per request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = dict(scope.get("headers") or []).get(b"x-request-id", b"").decode()[:64]
        rid = incoming if incoming.replace("-", "").replace("_", "").isalnum() else ""
        rid = rid or secrets.token_hex(8)
        token = request_id_var.set(rid)
        started = time.perf_counter()
        status = 500
        path = scope.get("path", "")
        is_api = path.startswith("/api/")

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", rid.encode()))
                headers.extend(SECURITY_HEADERS)
                if is_api and not any(k.lower() == b"cache-control" for k, _ in headers):
                    headers.append((b"cache-control", b"no-store"))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed = time.perf_counter() - started
            route = _route_template(scope)
            metrics.HTTP_REQUESTS.labels(scope.get("method", ""), route, str(status)).inc()
            metrics.HTTP_SECONDS.labels(route).observe(elapsed)
            if path not in _QUIET:
                log.info(
                    "request",
                    extra={
                        "method": scope.get("method"),
                        "path": path,
                        "status": status,
                        "ms": round(elapsed * 1000, 1),
                    },
                )
            request_id_var.reset(token)


_BODY_METHODS = {"POST", "PUT", "PATCH"}
# Uploading a SQLite database file is the one large body the API accepts.
_UPLOAD_PATH = re.compile(r"^/api/v1/projects/[^/]{1,64}/connections/sqlite$")
SESSION_COOKIE = "dbp_session"


def _bearer(headers: dict[bytes, bytes]) -> str:
    value = headers.get(b"authorization", b"").decode("latin-1")
    return value[7:].strip() if value.lower().startswith("bearer ") else ""


def _signed_in(headers: dict[bytes, bytes], secret: str) -> bool:
    """A genuine (signed, unexpired) session, checked without the database. The route still
    authorizes fully; this only decides whether a large body may be read at all."""
    token = _bearer(headers)
    if not token:
        for part in headers.get(b"cookie", b"").decode("latin-1").split(";"):
            name, _, value = part.strip().partition("=")
            if name == SESSION_COOKIE:
                token = value
    return bool(token) and decode_session(token, secret) is not None


class BodySizeLimitMiddleware:
    """Refuses request bodies over the limit with 413, whatever their framing.

    ``Content-Length`` is checked up front; chunked bodies are counted as they arrive and
    refused the moment they pass the limit, before any route parses them.
    """

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") not in _BODY_METHODS:
            await self.app(scope, receive, send)
            return
        limit = self.settings.max_body_bytes
        headers = dict(scope.get("headers") or [])
        if _UPLOAD_PATH.match(scope.get("path", "")) and _signed_in(
            headers, self.settings.session_secret
        ):
            limit = self.settings.max_upload_bytes
        length = headers.get(b"content-length", b"")
        if length.isdigit() and int(length) > limit:
            await self._refuse(send, limit)
            return
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            size += len(body)
            if size > limit:
                await self._refuse(send, limit)
                return
            chunks.append(body)
            if not message.get("more_body", False):
                break
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    async def _refuse(self, send: Send, limit: int) -> None:
        body = json.dumps(
            {
                "error": {
                    "code": "payload_too_large",
                    "message": f"The request body is larger than {limit:,} bytes",
                    "details": {"limit_bytes": limit},
                }
            }
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

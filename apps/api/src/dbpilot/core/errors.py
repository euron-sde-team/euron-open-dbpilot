"""Typed application errors and the single place they become HTTP responses: always
``{"error": {"code", "message", "details"}}``, never a stack trace."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from dbpilot.core.storage import ObjectNotFoundError

log = logging.getLogger("dbpilot.errors")


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.details = details or {}
        self.headers = headers or {}

    def body(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class AuthenticationError(AppError):
    status_code = 401
    code = "unauthenticated"


class PermissionDeniedError(AppError):
    status_code = 403
    code = "forbidden"


class GoneError(AppError):
    status_code = 410
    code = "gone"


class UnprocessableError(AppError):
    status_code = 422
    code = "unprocessable"


class RateLimitedError(AppError):
    status_code = 429
    code = "rate_limited"


class UpstreamError(AppError):
    """A dependency DBPilot calls out to (the model provider) failed."""

    status_code = 502
    code = "upstream_error"


def _validation_body(errors: list[dict[str, Any]]) -> dict[str, Any]:
    return AppError(
        "Request validation failed", code="validation_error", details={"errors": errors}
    ).body()


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(exc.body(), status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": list(e.get("loc", [])), "msg": e.get("msg", ""), "type": e.get("type", "")}
            for e in exc.errors()
        ]
        return JSONResponse(_validation_body(errors), status_code=422)

    @app.exception_handler(ValidationError)
    async def _model_validation(_: Request, exc: ValidationError) -> JSONResponse:
        # Models built inside a handler fail the same way as a request body: 422, never 500.
        errors = [
            {"loc": list(e.get("loc", [])), "msg": e.get("msg", ""), "type": e.get("type", "")}
            for e in exc.errors(include_url=False, include_input=False)
        ]
        return JSONResponse(_validation_body(errors), status_code=422)

    @app.exception_handler(IntegrityError)
    async def _integrity(request: Request, exc: IntegrityError) -> JSONResponse:
        # A uniqueness or reference race the service-level check could not see. The
        # constraint name is logged, never the values.
        log.warning(
            "integrity conflict", extra={"path": request.url.path, "constraint": _constraint(exc)}
        )
        err = ConflictError(
            "This conflicts with a change made at the same time. Reload and try again"
        )
        return JSONResponse(err.body(), status_code=409)

    @app.exception_handler(ObjectNotFoundError)
    async def _missing_object(_: Request, exc: ObjectNotFoundError) -> JSONResponse:
        err = GoneError("The stored file no longer exists", code="object_missing")
        return JSONResponse(err.body(), status_code=410)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "not_found" if exc.status_code == 404 else "http_error"
        err = AppError(str(exc.detail), code=code)
        return JSONResponse(err.body(), status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error", extra={"path": request.url.path})
        err = AppError("Internal server error", code="internal_error")
        return JSONResponse(err.body(), status_code=500)


def _constraint(exc: IntegrityError) -> str | None:
    diag = getattr(getattr(exc.orig, "__cause__", None), "constraint_name", None)
    return diag or getattr(exc.orig, "constraint_name", None)

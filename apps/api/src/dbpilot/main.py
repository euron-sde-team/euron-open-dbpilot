"""API application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from dbpilot import __version__
from dbpilot.ai.router import router as ai_router
from dbpilot.audit.router import router as audit_router
from dbpilot.auth.router import router as auth_router
from dbpilot.backups.router import router as backups_router
from dbpilot.comparisons.router import router as comparisons_router
from dbpilot.confirmations.router import router as confirmations_router
from dbpilot.connections.router import router as connections_router
from dbpilot.core.config import Settings, get_settings
from dbpilot.core.container import Container
from dbpilot.core.db import import_all_models
from dbpilot.core.errors import install_error_handlers
from dbpilot.core.logs import configure_logging
from dbpilot.core.middleware import BodySizeLimitMiddleware, RequestContextMiddleware
from dbpilot.core.tracing import configure_tracing
from dbpilot.dashboard.router import router as dashboard_router
from dbpilot.monitoring.router import router as monitoring_router
from dbpilot.orgs.router import router as orgs_router
from dbpilot.projects.router import router as projects_router
from dbpilot.queries.router import router as queries_router
from dbpilot.schema.router import router as schema_router
from dbpilot.system.router import router as system_router

DESCRIPTION = """
API of Euron Open DBPilot, a self-hosted, team-shared database operations dashboard for
PostgreSQL, MySQL, SQLite, SQL Server, MongoDB and Redis.

* Sign in with `POST /api/v1/auth/login` and send the session token as
  `Authorization: Bearer <token>` (the dashboard uses an HttpOnly cookie plus the
  `X-DBP-CSRF: 1` header on changes instead).
* Connection passwords are sealed server side and never returned.
* Every query runs read-only, enforced by the database itself where the engine can, after
  the SQL guard. AI-generated SQL only ever runs as one read-only query: anything else is
  refused for good.
* A deliberate write (`POST .../writes`), a migration (`POST .../comparisons/{id}/run`) and
  a restore (`POST .../backups/{id}/restore`) are only described at first; they run after a
  second, separate `POST .../confirmations/{id}/confirm`, audited before they run.
"""


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()
    import_all_models()
    configure_logging(settings.log_level)
    configure_tracing(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.container = container or Container.build(settings)
        try:
            yield
        finally:
            if container is None:
                await app.state.container.close()

    app = FastAPI(
        title="Euron Open DBPilot",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
        separate_input_output_schemas=False,
    )
    install_error_handlers(app)
    for router in (
        auth_router,
        orgs_router,
        projects_router,
        connections_router,
        schema_router,
        queries_router,
        confirmations_router,
        ai_router,
        monitoring_router,
        backups_router,
        comparisons_router,
        dashboard_router,
        audit_router,
        system_router,
    ):
        app.include_router(router)

    app.add_middleware(GZipMiddleware, minimum_size=2048)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["authorization", "content-type", "x-dbp-csrf", "x-request-id"],
        expose_headers=["x-request-id", "retry-after", "content-disposition"],
        max_age=600,
    )
    app.add_middleware(BodySizeLimitMiddleware, settings=settings)
    app.add_middleware(RequestContextMiddleware)
    return app

"""DBPilot's own database: engine, session factory and the declarative base every domain
shares. (Connected databases never go through this module: see connections/gateway.py.)"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_id() -> uuid.UUID:
    return uuid.uuid4()


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, list[str]: JSONB}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Database:
    """Owns the engine and session factory for one application instance."""

    def __init__(
        self,
        url: str,
        *,
        pool_size: int = 10,
        max_overflow: int = 20,
        pool_timeout_s: float = 10.0,
    ) -> None:
        self.engine: AsyncEngine = create_async_engine(
            url,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=pool_timeout_s,
            pool_pre_ping=True,
            pool_recycle=1800,
            # Never echo bound values (password hashes, statements, row data) into an
            # exception message, and so never into a log line.
            hide_parameters=True,
        )
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessionmaker() as session:
            yield session

    async def dispose(self) -> None:
        await self.engine.dispose()


def import_all_models() -> None:
    """Import every domain's models so Base.metadata is complete (Alembic, tests)."""
    from dbpilot.ai import models as _ai  # noqa: F401
    from dbpilot.audit import models as _audit  # noqa: F401
    from dbpilot.auth import models as _auth  # noqa: F401
    from dbpilot.backups import models as _backups  # noqa: F401
    from dbpilot.comparisons import models as _comparisons  # noqa: F401
    from dbpilot.confirmations import models as _confirmations  # noqa: F401
    from dbpilot.connections import models as _connections  # noqa: F401
    from dbpilot.orgs import models as _orgs  # noqa: F401
    from dbpilot.projects import models as _projects  # noqa: F401
    from dbpilot.queries import models as _queries  # noqa: F401
    from dbpilot.schema import models as _schema  # noqa: F401
    from dbpilot.system import models as _system  # noqa: F401

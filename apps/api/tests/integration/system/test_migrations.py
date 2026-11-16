"""The migrations build exactly what the models declare: autogenerate, run against the
database the migrations just built, finds nothing to change. A hand-written revision that
drifted from its model (a missing index, a wrong type) fails here."""

from __future__ import annotations

from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.ext.asyncio import create_async_engine

from dbpilot.core.db import Base, import_all_models

pytestmark = pytest.mark.integration


async def test_the_migrations_build_exactly_the_models(database: str) -> None:
    import_all_models()
    engine = create_async_engine(database)

    def diff(sync: Any) -> list[Any]:
        context = MigrationContext.configure(sync, opts={"compare_type": True})
        return list(compare_metadata(context, Base.metadata))

    async with engine.connect() as conn:
        changes = await conn.run_sync(diff)
    await engine.dispose()
    assert changes == []

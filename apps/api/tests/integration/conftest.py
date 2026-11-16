"""Integration harness: the real API served over HTTP by uvicorn on a thread, against real
Postgres and Redis, the mock model service, and real demo databases (the shop on
PostgreSQL, the people database on MySQL, SQLite files, and, when their containers run,
SQL Server, MongoDB and Redis sources).

Isolation: the suite drops and recreates its own database (``TEST_DATABASE_URL``, whose
name must contain "test") and uses its own Redis index (``TEST_REDIS_URL``). Demo data is
loaded into ``shop_test`` and ``people_test``, never into the databases ``make demo`` uses.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import pytest
import redis.asyncio as aioredis
import sqlalchemy as sa
import uvicorn
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from dbpilot.core.config import Settings

TEST_DB = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://dbpilot:dbpilot@localhost:5842/dbpilot_test"
)
TEST_REDIS = os.environ.get("TEST_REDIS_URL", "redis://localhost:5879/15")
SOURCE_PASSWORD = os.environ.get("TEST_SOURCE_ADMIN_PASSWORD", "test-only-source-admin-pass-1")
MSSQL_PASSWORD = os.environ.get("TEST_MSSQL_SA_PASSWORD", "Dbp-Test-Sql-Server-1")
READER = "dbpilot_reader"
READER_PASSWORD = "Reader-Test-Pass-1"
ADMIN = {"email": "admin@example.test", "password": "Admin-Pass-123"}
HOST = os.environ.get("TEST_SOURCE_HOST", "127.0.0.1")
PG_PORT = int(os.environ.get("TEST_SOURCE_PG_PORT", "5843"))
MYSQL_PORT = int(os.environ.get("TEST_SOURCE_MYSQL_PORT", "5806"))
MSSQL_PORT = int(os.environ.get("TEST_SOURCE_MSSQL_PORT", "5807"))
MONGO_PORT = int(os.environ.get("TEST_SOURCE_MONGO_PORT", "5808"))
REDIS_SOURCE_PORT = int(os.environ.get("TEST_SOURCE_REDIS_PORT", "5809"))
TODAY = dt.date(2026, 6, 15)


def reachable(port: int, host: str = HOST) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def _guard(url: str) -> None:
    parsed = urlparse(url.replace("+asyncpg", ""))
    if "test" not in (parsed.path or ""):
        raise RuntimeError(f"refusing {parsed.path!r}: the test database name must contain 'test'")
    if (
        parsed.hostname not in ("localhost", "127.0.0.1", "postgres")
        and os.environ.get("ALLOW_REMOTE_TEST_DB") != "true"
    ):
        raise RuntimeError("refusing a non-local test database (set ALLOW_REMOTE_TEST_DB=true)")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Served:
    """An ASGI app (or import string) served by uvicorn on a thread, with its own loop."""

    def __init__(self, app: Any) -> None:
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        config = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self) -> Served:
        self.thread.start()
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                if httpx.get(self.url + "/healthz", timeout=1).status_code < 500:
                    return self
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        raise RuntimeError(f"server on {self.url} did not start")

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=15)


@pytest.fixture(scope="session")
def mock_ai() -> Iterator[str]:
    served = Served("dbpilot.mockai.app:app").start()
    yield served.url
    served.stop()


@pytest.fixture(scope="session")
async def database() -> AsyncIterator[str]:
    """A fresh database built by the migrations (so they are tested on every run)."""
    _guard(TEST_DB)
    admin_url = TEST_DB.rsplit("/", 1)[0] + "/postgres"
    name = TEST_DB.rsplit("/", 1)[1]
    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    await engine.dispose()
    from dbpilot.migrations.runner import upgrade

    await asyncio.to_thread(upgrade, TEST_DB)
    yield TEST_DB


def admin_url(kind: str, database: str) -> str:
    if kind == "postgresql":
        return f"postgresql://shop_admin:{SOURCE_PASSWORD}@{HOST}:{PG_PORT}/{database}"
    if kind == "mysql":
        return f"mysql://root:{SOURCE_PASSWORD}@{HOST}:{MYSQL_PORT}/{database}"
    return f"mssql://sa:{MSSQL_PASSWORD}@{HOST}:{MSSQL_PORT}/{database}"


def source_engine(kind: str, database: str, *, autocommit: bool = False) -> sa.Engine:
    driver = {
        "postgresql": "postgresql+psycopg",
        "mysql": "mysql+pymysql",
        "mssql": "mssql+pymssql",
    }[kind]
    url = admin_url(kind, database).replace(f"{kind}://", f"{driver}://", 1)
    return (
        sa.create_engine(url, isolation_level="AUTOCOMMIT") if autocommit else sa.create_engine(url)
    )


def source_scalar(kind: str, database: str, sql: str) -> Any:
    engine = source_engine(kind, database)
    try:
        with engine.connect() as c:
            return c.exec_driver_sql(sql).scalar()
    finally:
        engine.dispose()


def recreate_pg_database(name: str) -> None:
    engine = source_engine("postgresql", "shop", autocommit=True)
    try:
        with engine.connect() as c:
            c.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            c.exec_driver_sql(f'CREATE DATABASE "{name}"')
    finally:
        engine.dispose()


def recreate_mysql_database(name: str) -> None:
    engine = source_engine("mysql", "people")
    try:
        with engine.begin() as c:
            c.exec_driver_sql(f"DROP DATABASE IF EXISTS {name}")
            c.exec_driver_sql(f"CREATE DATABASE {name}")
            cur = c.connection.dbapi_connection.cursor()  # type: ignore[union-attr]
            cur.execute(f"GRANT SELECT, SHOW VIEW ON {name}.* TO '{READER}'@'%'")
    finally:
        engine.dispose()


@dataclass(frozen=True)
class Sources:
    sqlite_path: Path

    def pg(
        self, *, admin: bool = False, database: str = "shop_test", **extra: Any
    ) -> dict[str, Any]:
        return {
            "name": f"Shop {'admin' if admin else 'reader'} {database}",
            "kind": "postgresql",
            "host": HOST,
            "port": PG_PORT,
            "database": database,
            "username": "shop_admin" if admin else READER,
            "password": SOURCE_PASSWORD if admin else READER_PASSWORD,
            "ssl_mode": "disable",
            **extra,
        }

    def mysql(
        self, *, admin: bool = False, database: str = "people_test", **extra: Any
    ) -> dict[str, Any]:
        return {
            "name": f"People {'admin' if admin else 'reader'} {database}",
            "kind": "mysql",
            "host": HOST,
            "port": MYSQL_PORT,
            "database": database,
            "username": "root" if admin else READER,
            "password": SOURCE_PASSWORD if admin else READER_PASSWORD,
            "ssl_mode": "disable",
            **extra,
        }


@pytest.fixture(scope="session")
def sources(tmp_path_factory: pytest.TempPathFactory) -> Sources:
    from dbpilot.demo import sql_sources

    engine = source_engine("postgresql", "shop", autocommit=True)
    with engine.connect() as c:
        if not c.exec_driver_sql("SELECT 1 FROM pg_database WHERE datname = 'shop_test'").scalar():
            c.exec_driver_sql("CREATE DATABASE shop_test")
    engine.dispose()
    my = source_engine("mysql", "people")
    with my.begin() as c:
        c.exec_driver_sql("CREATE DATABASE IF NOT EXISTS people_test")
    my.dispose()
    sql_sources.build_shop(admin_url("postgresql", "shop_test"), READER_PASSWORD, TODAY)
    sql_sources.build_people(admin_url("mysql", "people_test"), READER_PASSWORD, TODAY)
    path = sql_sources.build_subscriptions(
        tmp_path_factory.mktemp("sources") / "subscriptions.sqlite", TODAY
    )
    return Sources(sqlite_path=path)


def make_settings(database: str, tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": "test",
        "database_url": database,
        "redis_url": TEST_REDIS,
        "session_secret": "test-only-session-secret-with-32-plus-bytes",
        "local_master_key": "test-only-master-key-with-32-plus-bytes-ok",
        "local_storage_dir": str(tmp_path / "objects"),
        "work_dir": str(tmp_path / "work"),
        "ai_provider": "mock",
        "connection_test_rpm": 10_000,
        "login_rpm_per_ip": 10_000,
        "key_mode": os.environ.get("TEST_KEY_MODE", "local"),
        "storage": os.environ.get("TEST_STORAGE", "local"),
        "aws_endpoint_url": os.environ.get("TEST_AWS_ENDPOINT"),
        "s3_force_path_style": True,
        "kms_key_id": os.environ.get("TEST_KMS_KEY_ID", "alias/dbpilot-local"),
        "objects_bucket": os.environ.get("TEST_OBJECTS_BUCKET", "dbpilot-local-objects"),
    }
    values.update(overrides)
    return Settings(**values)


async def reset(database: str) -> None:
    """Empty every table except the append-only audit log, and the Redis DB."""
    engine = create_async_engine(database)
    async with engine.begin() as conn:
        tables = (
            await conn.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                    "AND tablename NOT IN ('alembic_version', 'audit_log')"
                )
            )
        ).scalars()
        names = ", ".join(f'"{t}"' for t in tables)
        if names:
            await conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    await engine.dispose()
    r = aioredis.Redis.from_url(TEST_REDIS)
    await r.flushdb()
    await r.aclose()


async def bootstrap_admin(database: str) -> None:
    from dbpilot.audit.service import Actor
    from dbpilot.core.db import Database
    from dbpilot.core.security import hash_password
    from dbpilot.orgs import service as orgs
    from dbpilot.orgs.models import User

    db = Database(database, pool_size=1, max_overflow=0)
    async with db.sessionmaker() as session:
        user = User(
            email=ADMIN["email"],
            name="Admin",
            role="admin",
            password_hash=hash_password(ADMIN["password"]),
        )
        session.add(user)
        await session.flush()
        await orgs.create_for(session, user, "Acme", Actor(user.id, user.email, "admin"))
        await session.commit()
    await db.dispose()


async def login(base_url: str, creds: dict[str, str] = ADMIN) -> httpx.AsyncClient:
    client = httpx.AsyncClient(base_url=base_url, timeout=180)
    resp = await client.post("/api/v1/auth/login", json=creds)
    assert resp.status_code == 200, resp.text
    client.headers["authorization"] = f"Bearer {resp.json()['session_token']}"
    return client


@dataclass
class Stack:
    """The running API, a signed-in instance admin, the current project, and helpers."""

    api_url: str
    settings: Settings
    client: httpx.AsyncClient
    mock_ai_url: str
    org_id: str
    project_id: str = ""

    async def ok(
        self,
        method: str,
        path: str,
        json: Any = None,
        status: int | None = None,
        client: httpx.AsyncClient | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        c = client or self.client
        resp = await c.request(method, path, json=json, params=params)
        expected = status or (201 if method == "POST" and resp.status_code == 201 else 200)
        assert resp.status_code == expected, f"{method} {path}: {resp.status_code} {resp.text}"
        if not resp.content:
            return None
        if "json" not in resp.headers.get("content-type", ""):
            return resp.content
        return resp.json()

    def p(self, path: str = "") -> str:
        return f"/api/v1/projects/{self.project_id}{path}"

    async def project(self, name: str = "Operations") -> dict[str, Any]:
        out = dict(
            await self.ok(
                "POST", "/api/v1/projects", {"org_id": self.org_id, "name": name}, status=201
            )
        )
        self.project_id = out["id"]
        return out

    async def connection(self, body: dict[str, Any], **changes: Any) -> dict[str, Any]:
        return dict(await self.ok("POST", self.p("/connections"), {**body, **changes}, status=201))

    async def sqlite(self, path: Path, name: str = "Subscriptions (SQLite)") -> dict[str, Any]:
        with path.open("rb") as f:
            resp = await self.client.post(
                self.p("/connections/sqlite"),
                data={"name": name, "sample_values": "true"},
                files={"file": ("subscriptions.sqlite", f, "application/vnd.sqlite3")},
            )
        assert resp.status_code == 201, resp.text
        return dict(resp.json())

    async def sql(
        self, connection_id: str, sql: str, client: httpx.AsyncClient | None = None
    ) -> dict[str, Any]:
        return dict(
            await self.ok(
                "POST",
                self.p("/queries/sql"),
                {"connection_id": connection_id, "sql": sql},
                client=client,
            )
        )

    async def audit(self, **params: Any) -> list[dict[str, Any]]:
        return list(
            (await self.ok("GET", self.p("/audit"), params={"limit": 200, **params}))["items"]
        )

    async def user_in_org(
        self, role: str = "member", email: str | None = None
    ) -> tuple[httpx.AsyncClient, str]:
        email = email or f"{role}-{time.monotonic_ns()}@example.test"
        password = "User-Pass-123"
        await self.ok(
            "POST",
            f"/api/v1/orgs/{self.org_id}/members",
            {"email": email, "role": role, "name": "Test User", "password": password},
            status=201,
        )
        client = await login(self.api_url, {"email": email, "password": password})
        me = (await client.get("/api/v1/auth/me")).json()
        return client, str(me["id"])

    async def member(self, role: str) -> tuple[httpx.AsyncClient, str]:
        """A person in the organization with ``role`` in the current project."""
        client, user_id = await self.user_in_org("member")
        me = (await client.get("/api/v1/auth/me")).json()
        await self.ok("POST", self.p("/members"), {"email": me["email"], "role": role}, status=201)
        return client, user_id

    async def confirm(
        self, prepared: dict[str, Any], client: httpx.AsyncClient | None = None, status: int = 200
    ) -> Any:
        return await self.ok(
            "POST",
            self.p(f"/confirmations/{prepared['id']}/confirm"),
            {"confirm_token": prepared["confirm_token"], "digest": prepared["digest"]},
            client=client,
            status=status,
        )


@pytest.fixture
def settings_overrides() -> dict[str, Any]:
    """Tests override this to change the API's settings."""
    return {}


@pytest.fixture
async def stack(
    database: str, tmp_path: Path, mock_ai: str, settings_overrides: dict[str, Any]
) -> AsyncIterator[Stack]:
    from dbpilot.main import create_app

    await reset(database)
    await bootstrap_admin(database)
    settings = make_settings(database, tmp_path, **settings_overrides)
    served = Served(create_app(settings)).start()
    client = await login(served.url)
    org_id = (await client.get("/api/v1/orgs")).json()[0]["id"]
    async with httpx.AsyncClient() as http:
        await http.post(f"{mock_ai}/_mock/reset")
    stack = Stack(served.url, settings, client, mock_ai, org_id)
    try:
        yield stack
    finally:
        await client.aclose()
        served.stop()


@pytest.fixture
async def ready(stack: Stack) -> Stack:
    """A stack with a project."""
    await stack.project()
    return stack

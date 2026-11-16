"""``dbpilot seed``: the demo databases, an organization with people in every role, their
connections and grants, and real history (queries, AI generations accepted and refused, a
confirmed write, a backup, a schedule, a schema comparison) so every screen has something
true to show. Everything goes through the same services the API uses.

Idempotent: when the demo organization exists it only prints the sign-in details.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import tempfile
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import UploadFile
from sqlalchemy import select

from dbpilot.ai import service as ai
from dbpilot.audit.service import Actor
from dbpilot.backups import schedules
from dbpilot.backups.schemas import ScheduleIn
from dbpilot.backups.service import run_backup
from dbpilot.comparisons import service as comparisons
from dbpilot.comparisons.schemas import ComparisonIn
from dbpilot.confirmations import service as confirmations
from dbpilot.connections import service as connections
from dbpilot.connections.access import accessible
from dbpilot.connections.schemas import ConnectionCreate, GrantIn, Limits
from dbpilot.core.config import Settings, get_settings
from dbpilot.core.container import Container
from dbpilot.core.db import import_all_models
from dbpilot.core.security import hash_password
from dbpilot.demo import nosql_sources, sql_sources
from dbpilot.orgs import service as orgs
from dbpilot.orgs.models import Organization, OrgMember, User
from dbpilot.projects.access import Principal
from dbpilot.projects.models import Project, ProjectMember
from dbpilot.queries import service as queries
from dbpilot.queries import writes

ORG_NAME = "Lumen Logistics"
ORG_SLUG = "lumen-logistics"
PEOPLE = [
    ("maya.chen@lumenlogistics.example", "Maya Chen", "owner", "owner"),
    ("arjun.rao@lumenlogistics.example", "Arjun Rao", "member", "admin"),
    ("sofia.alvarez@lumenlogistics.example", "Sofia Alvarez", "member", "viewer"),
    ("liam.okafor@lumenlogistics.example", "Liam Okafor", "member", "viewer"),
]


def _logins(settings: Settings) -> None:
    print("Demo sign-in (password for every account: the DBPILOT_DEMO_PASSWORD value)")
    for email, name, _, role in PEOPLE:
        print(f"  {email:42} {name:15} project {role}")
    print(f"  password: {settings.demo_password}")


def _server(
    name: str,
    description: str,
    url: str,
    kind: str,
    database: str,
    password: str,
    *,
    admin: bool = False,
    **extra: Any,
) -> ConnectionCreate:
    parts = urlsplit(url)
    return ConnectionCreate(
        name=name,
        description=description,
        kind=kind,
        host=parts.hostname or "localhost",
        port=parts.port,
        database=database,
        username=(parts.username or "") if admin else sql_sources.READER,
        password=(parts.password or "") if admin else password,
        ssl_mode="disable",
        sample_values=True,
        **extra,
    )


async def run() -> None:
    settings = get_settings()
    import_all_models()
    container = Container.build(settings)
    try:
        async with container.db.sessionmaker() as session:
            if await session.scalar(select(Organization.id).where(Organization.slug == ORG_SLUG)):
                print("The demo organization exists already.")
                _logins(settings)
                return
        await _seed(container, settings)
        _logins(settings)
    finally:
        await container.close()


async def _seed(container: Container, settings: Settings) -> None:
    reader_pw = settings.demo_reader_password
    today = dt.date.today()
    if settings.demo_source_pg_admin:
        base = settings.demo_source_pg_admin
        await asyncio.to_thread(sql_sources.build_shop, base, reader_pw, today)
        server_admin = base.rsplit("/", 1)[0] + "/postgres"
        import sqlalchemy as sa

        def make_staging() -> None:
            engine = sa.create_engine(
                server_admin.replace("postgresql://", "postgresql+psycopg://", 1),
                isolation_level="AUTOCOMMIT",
            )
            with engine.connect() as c:
                if not c.exec_driver_sql(
                    "SELECT 1 FROM pg_database WHERE datname = 'shop_staging'"
                ).scalar():
                    c.exec_driver_sql("CREATE DATABASE shop_staging")
            engine.dispose()

        await asyncio.to_thread(make_staging)
        await asyncio.to_thread(
            sql_sources.build_shop, base.rsplit("/", 1)[0] + "/shop_staging", reader_pw, today
        )
    if settings.demo_source_mysql_admin:
        await asyncio.to_thread(
            sql_sources.build_people, settings.demo_source_mysql_admin, reader_pw, today
        )
    if settings.demo_source_mssql_admin:
        await asyncio.to_thread(
            sql_sources.build_inventory, settings.demo_source_mssql_admin, "inventory", reader_pw
        )
    if settings.demo_source_mongo_admin:
        await asyncio.to_thread(
            nosql_sources.build_catalog, settings.demo_source_mongo_admin, "catalog", reader_pw
        )
    if settings.demo_source_redis_url:
        await asyncio.to_thread(nosql_sources.build_sessions, settings.demo_source_redis_url)

    async with container.db.sessionmaker() as session:
        users: dict[str, User] = {}
        for email, name, _, _ in PEOPLE:
            user = await orgs.user_by_email(session, email) or User(
                email=email,
                name=name,
                role="user",
                password_hash=hash_password(settings.demo_password),
            )
            session.add(user)
            users[email] = user
        await session.flush()
        owner = users[PEOPLE[0][0]]
        actor = Actor(owner.id, owner.email, owner.role)
        org = await orgs.create_for(session, owner, ORG_NAME, actor)
        org.slug = ORG_SLUG
        for email, _, org_role, _ in PEOPLE[1:]:
            session.add(OrgMember(org_id=org.id, user_id=users[email].id, role=org_role))
        project = Project(
            org_id=org.id,
            name="Operations",
            slug="operations",
            description="Production and staging databases of the logistics platform",
            created_by=owner.id,
        )
        session.add(project)
        await session.flush()
        for email, _, _, role in PEOPLE:
            session.add(ProjectMember(project_id=project.id, user_id=users[email].id, role=role))
        await session.commit()

    def principal(email: str, role: str) -> Principal:
        u = users[email]
        return Principal(project, u, role, Actor(u.id, u.email, u.role))

    maya = principal(PEOPLE[0][0], "owner")
    arjun = principal(PEOPLE[1][0], "admin")
    sofia = principal(PEOPLE[2][0], "viewer")
    made: dict[str, uuid.UUID] = {}
    async with container.db.sessionmaker() as session:
        specs: list[ConnectionCreate] = []
        if settings.demo_source_pg_admin:
            specs.append(
                _server(
                    "Shop (PostgreSQL)",
                    "Orders, customers and products of the web shop",
                    settings.demo_source_pg_admin,
                    "postgresql",
                    "shop",
                    reader_pw,
                )
            )
            specs.append(
                _server(
                    "Shop staging (PostgreSQL, read-write login)",
                    "Staging copy where schema changes are tried first",
                    settings.demo_source_pg_admin,
                    "postgresql",
                    "shop_staging",
                    reader_pw,
                    admin=True,
                )
            )
        if settings.demo_source_mysql_admin:
            specs.append(
                _server(
                    "People (MySQL)",
                    "Departments, employees and leave requests",
                    settings.demo_source_mysql_admin,
                    "mysql",
                    "people",
                    reader_pw,
                )
            )
        if settings.demo_source_mssql_admin:
            specs.append(
                _server(
                    "Inventory (SQL Server)",
                    "Warehouses, items and stock levels",
                    settings.demo_source_mssql_admin,
                    "mssql",
                    "inventory",
                    reader_pw,
                )
            )
        if settings.demo_source_mongo_admin:
            specs.append(
                _server(
                    "Catalog (MongoDB)",
                    "Product catalog and reviews",
                    settings.demo_source_mongo_admin,
                    "mongodb",
                    "catalog",
                    reader_pw,
                    auth_source="catalog",
                )
            )
        if settings.demo_source_redis_url:
            parts = urlsplit(settings.demo_source_redis_url)
            specs.append(
                ConnectionCreate(
                    name="Sessions (Redis)",
                    description="Web sessions, carts and rate counters",
                    kind="redis",
                    host=parts.hostname or "localhost",
                    port=parts.port,
                    database=parts.path.strip("/") or "0",  # the database the URL names
                    password=parts.password or None,
                )
            )
        for spec in specs:
            conn = await connections.create_server(container, session, maya, spec)
            made[spec.name] = conn.id
        with tempfile.TemporaryDirectory() as tmp:
            path = sql_sources.build_subscriptions(Path(tmp) / "subscriptions.sqlite", today)
            with path.open("rb") as f:
                upload = UploadFile(file=f, filename="subscriptions.sqlite")
                conn = await connections.create_sqlite(
                    container,
                    session,
                    maya,
                    "Subscriptions (SQLite)",
                    upload,
                    Limits(),
                    sample_values=True,
                    description="Plans, accounts and invoices of the SaaS business",
                )
                made["Subscriptions (SQLite)"] = conn.id
        for name in ("Shop (PostgreSQL)", "People (MySQL)"):
            if name in made:
                conn = (await accessible(session, maya, made[name])).connection
                await connections.put_grant(
                    session, maya, conn, GrantIn(user_id=sofia.user_id, can_write=False)
                )
        if "Catalog (MongoDB)" in made:
            conn = (await accessible(session, maya, made["Catalog (MongoDB)"])).connection
            await connections.put_grant(
                session, maya, conn, GrantIn(user_id=users[PEOPLE[3][0]].id, can_write=False)
            )
        conn = (await accessible(session, maya, made["Subscriptions (SQLite)"])).connection
        await connections.put_grant(session, maya, conn, GrantIn(user_id=None, can_write=False))
        for cid in made.values():
            await connections.test(
                container, session, maya, (await accessible(session, maya, cid)).connection
            )

    async with container.db.sessionmaker() as session:
        if "Shop (PostgreSQL)" in made:
            shop = await accessible(session, sofia, made["Shop (PostgreSQL)"])
            for sql in (
                "SELECT status, count(*) AS orders FROM orders GROUP BY status ORDER BY orders DESC",
                "SELECT c.country, round(sum(o.total_amount), 2) AS revenue FROM orders o JOIN customers c ON c.id = o.customer_id GROUP BY c.country ORDER BY revenue DESC",
                "SELECT * FROM monthly_revenue ORDER BY month DESC LIMIT 12",
            ):
                await queries.run_sql(container, session, sofia, shop, sql)
            await queries.run_sql(
                container, session, sofia, shop, "DELETE FROM orders WHERE status = 'cancelled'"
            )
            for question in (
                "total revenue by country",
                "how many orders per status",
                "Delete all cancelled orders",
            ):
                g = await ai.generate_sql(container, session, sofia, shop, question)
                if g.status == "accepted":
                    await ai.run_generation(container, session, sofia, g.id)
            await run_backup(
                container,
                session,
                (await accessible(session, maya, made["Shop (PostgreSQL)"])).connection,
                maya.actor,
                org_id=maya.org_id,
                trigger="manual",
            )
            await schedules.create(
                session,
                arjun,
                ScheduleIn(
                    connection_id=made["Shop (PostgreSQL)"],
                    interval_minutes=1440,
                    retention_count=7,
                ),
            )
        if "People (MySQL)" in made:
            people = await accessible(session, sofia, made["People (MySQL)"])
            await queries.run_sql(
                container,
                session,
                sofia,
                people,
                "SELECT d.name, count(*) AS headcount, round(avg(e.salary)) AS avg_salary FROM employees e JOIN departments d ON d.id = e.department_id GROUP BY d.name ORDER BY headcount DESC",
            )
        staging_name = "Shop staging (PostgreSQL, read-write login)"
        if staging_name in made and "Shop (PostgreSQL)" in made:
            staging = await accessible(session, arjun, made[staging_name])
            c, token = await writes.prepare(
                container,
                session,
                arjun,
                staging,
                "ALTER TABLE customers ADD COLUMN loyalty_tier varchar(20)",
                note="Try the loyalty tier column on staging first",
            )
            c, conn = await confirmations.begin(
                session, arjun, c.id, token, c.digest, can_write=True
            )
            await writes.execute(container, session, arjun, c, conn)
            await comparisons.create(
                container,
                session,
                arjun,
                ComparisonIn(
                    from_connection_id=made["Shop (PostgreSQL)"],
                    to_connection_id=made[staging_name],
                ),
            )
            await writes.prepare(
                container,
                session,
                arjun,
                staging,
                "UPDATE customers SET loyalty_tier = 'gold' WHERE id IN (SELECT customer_id FROM orders GROUP BY customer_id HAVING count(*) > 12)",
                note="Backfill gold tier (awaiting confirmation)",
            )

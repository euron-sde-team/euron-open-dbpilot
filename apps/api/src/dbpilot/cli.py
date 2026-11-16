"""The ``dbpilot`` command: every service and admin task in one entry point.

dbpilot serve          the API
dbpilot worker         the scheduled-backup worker (dbpilot/worker.py)
dbpilot migrate        apply database migrations
dbpilot revision -m    autogenerate a migration from the models
dbpilot create-admin   create or reset an instance administrator
dbpilot seed           demo databases, organization, people, connections and history
dbpilot openapi        print the OpenAPI document as YAML
dbpilot mockai         the deterministic mock model provider (tests, demo)
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dbpilot.core.config import Settings


def _settings() -> Settings:
    from dbpilot.core.config import get_settings

    return get_settings()


def _uvicorn(target: str, args: argparse.Namespace, *, factory: bool) -> None:
    import uvicorn

    uvicorn.run(
        target,
        factory=factory,
        host=args.host,
        port=args.port,
        workers=1,  # one process per container: metrics and in-process state stay exact
        proxy_headers=True,
        # Only trust X-Forwarded-For from the load balancer's network (the VPC CIDR on AWS).
        forwarded_allow_ips=_settings().forwarded_allow_ips,
        access_log=False,
        log_config=None,
        timeout_graceful_shutdown=30,
    )


def _serve(args: argparse.Namespace) -> None:
    _uvicorn("dbpilot.main:create_app", args, factory=True)


def _mockai(args: argparse.Namespace) -> None:
    _uvicorn("dbpilot.mockai.app:app", args, factory=False)


def _worker(_: argparse.Namespace) -> None:
    from dbpilot.worker import main

    asyncio.run(main())


def _migrate(_: argparse.Namespace) -> None:
    from dbpilot.core.config import get_settings
    from dbpilot.migrations.runner import upgrade

    upgrade(get_settings().database_url)
    print("migrations applied")


def _revision(args: argparse.Namespace) -> None:
    from dbpilot.core.config import get_settings
    from dbpilot.migrations.runner import revision

    revision(get_settings().database_url, args.message)


async def _create_admin(email: str, name: str, password: str) -> None:
    from dbpilot.audit.service import Actor
    from dbpilot.core.config import get_settings
    from dbpilot.core.db import Database, import_all_models
    from dbpilot.core.security import hash_password, password_problems
    from dbpilot.orgs import service as orgs
    from dbpilot.orgs.models import User

    if problems := password_problems(password):
        sys.exit("password needs " + ", ".join(problems))
    import_all_models()
    db = Database(get_settings().database_url, pool_size=1, max_overflow=0)
    try:
        async with db.sessionmaker() as session:
            existing = await orgs.user_by_email(session, email)
            if existing:
                existing.role = "admin"
                existing.password_hash = hash_password(password)
                existing.disabled = False
                existing.session_version += 1
                print(f"updated {email} (admin)")
            else:
                user = User(
                    email=email.lower(),
                    name=name,
                    role="admin",
                    password_hash=hash_password(password),
                )
                session.add(user)
                await session.flush()
                # Every account starts with an organization of its own.
                await orgs.create_for(
                    session, user, f"{name}'s organization", Actor(user.id, user.email, "admin")
                )
                print(f"created {email} (admin)")
            await session.commit()
    finally:
        await db.dispose()


def _admin(args: argparse.Namespace) -> None:
    password = _settings().admin_password or getpass.getpass("Password: ")
    asyncio.run(_create_admin(args.email, args.name, password))


def _seed(_: argparse.Namespace) -> None:
    from dbpilot.demo.seed import run

    asyncio.run(run())


def _openapi(_: argparse.Namespace) -> None:
    from dbpilot.openapi import spec, to_yaml

    sys.stdout.write(to_yaml(spec()))


def _server(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
    name: str,
    fn: object,
    help_: str,
    default_port: str,
) -> None:
    p = sub.add_parser(name, help=help_)
    p.add_argument("--host", default="0.0.0.0")  # noqa: S104 containers listen on all interfaces
    p.add_argument("--port", type=int, default=None, help=f"default: PORT, else {default_port}")
    p.set_defaults(default_port=int(default_port))
    p.set_defaults(fn=fn)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dbpilot", description="Euron Open DBPilot")
    sub = parser.add_subparsers(dest="command", required=True)
    _server(sub, "serve", _serve, "the API", "8080")
    _server(sub, "mockai", _mockai, "the deterministic mock model provider", "5650")
    sub.add_parser(
        "worker", help="the scheduled-backup worker: due schedules, retention, housekeeping"
    ).set_defaults(fn=_worker)
    sub.add_parser("migrate", help="apply database migrations").set_defaults(fn=_migrate)
    p = sub.add_parser("revision", help="autogenerate a migration from the models")
    p.add_argument("-m", "--message", required=True)
    p.set_defaults(fn=_revision)
    p = sub.add_parser("create-admin", help="create or reset an instance administrator")
    p.add_argument("--email", required=True)
    p.add_argument("--name", default="Admin")
    p.set_defaults(fn=_admin)
    sub.add_parser(
        "seed",
        aliases=["seed-demo"],
        help="demo databases, organization, people, connections and history (idempotent)",
    ).set_defaults(fn=_seed)
    sub.add_parser("openapi", help="print the OpenAPI document as YAML").set_defaults(fn=_openapi)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv if argv is not None else sys.argv[1:])
    if getattr(args, "port", "absent") is None:
        # PORT from the environment (through Settings) for the API; the server's own
        # default for the mock model provider, so both can run side by side.
        args.port = _settings().port if args.command == "serve" else args.default_port
    args.fn(args)


if __name__ == "__main__":
    main()

"""What is needed to open a connected database: plain data, built by the gateway at the
moment of use (the sealed password is opened then and lives only in these objects, which
never print it). ``address`` is the IP address the gateway vetted for ``host``: the openers
connect to it, keeping ``host`` as the name TLS verifies."""

from __future__ import annotations

from dataclasses import dataclass, field

SQL_KINDS = ("postgresql", "mysql", "mssql", "sqlite")
SERVER_SQL_KINDS = ("postgresql", "mysql", "mssql")
ALL_KINDS = ("postgresql", "mysql", "sqlite", "mssql", "mongodb", "redis")
DEFAULT_PORTS = {"postgresql": 5432, "mysql": 3306, "mssql": 1433, "mongodb": 27017, "redis": 6379}
APP_NAME = "euron-dbpilot"


@dataclass(frozen=True)
class SqlTarget:
    kind: str  # postgresql, mysql, mssql, sqlite
    host: str = ""
    port: int | None = None
    database: str = ""
    username: str = ""
    password: str | None = field(default=None, repr=False)
    ssl_mode: str = "prefer"
    sqlite_path: str | None = None
    default_schema: str | None = None
    address: str | None = None


@dataclass(frozen=True)
class MongoTarget:
    host: str
    port: int
    database: str
    username: str = ""
    password: str | None = field(default=None, repr=False)
    auth_source: str = "admin"
    ssl_mode: str = "disable"
    address: str | None = None


@dataclass(frozen=True)
class RedisTarget:
    host: str
    port: int
    db: int = 0
    username: str = ""
    password: str | None = field(default=None, repr=False)
    ssl_mode: str = "disable"
    address: str | None = None


Target = SqlTarget | MongoTarget | RedisTarget


def connect_host(t: SqlTarget | MongoTarget | RedisTarget) -> str:
    """Where a driver that verifies TLS by the name it connected to should connect: the
    vetted address, unless TLS verifies the host name (then a name that resolves elsewhere
    fails the certificate check before any password is sent)."""
    if t.address and t.ssl_mode != "verify-full":
        return t.address
    return t.host

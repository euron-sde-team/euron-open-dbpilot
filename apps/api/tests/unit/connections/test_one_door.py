"""AC10, structurally: the gateway is the only door to connected databases.

No module but ``connections/gateway.py`` imports the openers, and nothing outside the
openers creates a client to a connected database. (DBPilot's own Postgres and Redis live
in ``core/`` and ``migrations/``; the demo builders in ``demo/`` set up the demo
databases as their administrators, outside the product's connection model.)
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[3] / "src" / "dbpilot"
OPENERS = "dbpilot.connections.connectors.openers"
GATEWAY = SRC / "connections" / "gateway.py"
DRIVER_MODULES = {
    "sqlite3",
    "psycopg",
    "psycopg2",
    "pymysql",
    "pymssql",
    "asyncpg",
    "pymongo",
    "redis",
}
CLIENT_FACTORIES = {"create_engine", "create_async_engine", "MongoClient", "Redis", "StrictRedis"}
ALLOWED_CLIENT_MODULES = {
    SRC / "connections" / "connectors" / "openers.py",
    SRC / "core" / "db.py",
    SRC / "core" / "kv.py",
    SRC / "migrations" / "env.py",
}


def _modules() -> list[Path]:
    return [p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts]


def _imports(tree: ast.AST, path: Path) -> set[str]:
    out: set[str] = set()
    package = ".".join(path.relative_to(SRC.parent).with_suffix("").parts[:-1])
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".")
                base = ".".join(parts[: len(parts) - node.level + 1] + ([base] if base else []))
            out.add(base)
            out |= {f"{base}.{a.name}" for a in node.names}
    return out


def test_only_the_gateway_imports_the_openers() -> None:
    importers = sorted(
        str(p.relative_to(SRC))
        for p in _modules()
        if p != SRC / "connections" / "connectors" / "openers.py"
        and any(
            i == OPENERS or i.startswith(OPENERS + ".")
            for i in _imports(ast.parse(p.read_text()), p)
        )
    )
    assert importers == ["connections/gateway.py"]


def _calls(tree: ast.AST) -> set[str]:
    """Calls that create a database client: an engine or client factory, a driver
    module's ``connect`` (``sqlite3.connect``), or ``Redis.from_url``."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in CLIENT_FACTORIES:
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            owner = func.value.id if isinstance(func.value, ast.Name) else ""
            if func.attr in CLIENT_FACTORIES:
                names.add(func.attr)
            elif func.attr == "connect" and owner in DRIVER_MODULES:
                names.add(f"{owner}.connect")
            elif func.attr == "from_url" and owner in ("Redis", "redis", "StrictRedis"):
                names.add(f"{owner}.from_url")
    return names


def test_nothing_else_creates_a_database_client() -> None:
    offenders = []
    for p in _modules():
        if p in ALLOWED_CLIENT_MODULES or "demo" in p.relative_to(SRC).parts:
            continue
        found = _calls(ast.parse(p.read_text()))
        if found:
            offenders.append(f"{p.relative_to(SRC)}: {sorted(found)}")
    assert offenders == []


def test_the_gateway_records_an_audit_entry_in_every_outcome() -> None:
    """Both doors finish in ``_finish`` from a ``finally`` block, and ``_finish`` calls the
    one audit function."""
    tree = ast.parse(GATEWAY.read_text())
    functions = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)}
    for door in ("read", "write"):
        tries = [n for n in ast.walk(functions[door]) if isinstance(n, ast.Try)]
        assert any(
            any(isinstance(s, ast.Expr) and "_finish" in ast.unparse(s) for s in t.finalbody)
            for t in tries
        ), door
    assert "record(" in ast.unparse(functions["_finish"])

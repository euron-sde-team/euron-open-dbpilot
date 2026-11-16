"""Connections: AC1 (the secret is sealed, never returned, opened only when used), CRUD,
testing before saving, and the host checks."""

from __future__ import annotations

import json

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from tests.integration.conftest import READER_PASSWORD, Sources, Stack

pytestmark = pytest.mark.integration


async def _row(stack: Stack, connection_id: str) -> tuple[bytes | None, str]:
    engine = create_async_engine(stack.settings.database_url)
    async with engine.connect() as c:
        sealed, config = (
            await c.execute(
                sa.text("SELECT secret_sealed, config::text FROM connections WHERE id = :id"),
                {"id": connection_id},
            )
        ).one()
    await engine.dispose()
    return (bytes(sealed) if sealed is not None else None), str(config)


async def test_ac1_password_is_sealed_never_returned_and_is_what_opens_the_database(
    ready: Stack, sources: Sources
) -> None:
    body = sources.pg()
    conn = await ready.connection(body)
    assert conn["has_password"] is True
    assert "password" not in conn and "secret_sealed" not in conn
    for path in ("/connections", f"/connections/{conn['id']}"):
        assert body["password"] not in (await ready.client.get(ready.p(path))).text
    sealed, config = await _row(ready, conn["id"])
    assert sealed is not None and body["password"].encode() not in sealed
    assert body["password"] not in config
    # The sealed password is the credential that opens the database.
    ok = await ready.ok("POST", ready.p(f"/connections/{conn['id']}/test"))
    assert ok["ok"] is True and ok["read_only"] is True
    assert ok["server_version"].startswith("PostgreSQL 16") and ok["latency_ms"] > 0
    # It never appears in the audit trail either.
    assert all(body["password"] not in json.dumps(e) for e in await ready.audit())
    # A wrong stored password is refused by the database itself.
    await ready.ok("PATCH", ready.p(f"/connections/{conn['id']}"), {"password": "wrong-password"})
    bad = await ready.ok("POST", ready.p(f"/connections/{conn['id']}/test"))
    assert bad["ok"] is False and "password was refused" in bad["message"]


async def test_ac1_a_sealed_secret_copied_to_another_record_does_not_open(
    ready: Stack, sources: Sources
) -> None:
    first = await ready.connection(sources.pg())
    second = await ready.connection(sources.pg(name="Second", password="Other-Pass-1"))
    engine = create_async_engine(ready.settings.database_url)
    async with engine.begin() as c:
        await c.execute(
            sa.text(
                "UPDATE connections SET secret_sealed = (SELECT secret_sealed FROM connections "
                "WHERE id = :a) WHERE id = :b"
            ),
            {"a": first["id"], "b": second["id"]},
        )
    await engine.dispose()
    out = await ready.ok("POST", ready.p(f"/connections/{second['id']}/test"))
    assert out["ok"] is False  # the encryption context is bound to the record id


async def test_testing_before_saving_stores_nothing_and_is_audited(
    ready: Stack, sources: Sources
) -> None:
    out = await ready.ok("POST", ready.p("/connections/test"), sources.mysql())
    assert out["ok"] is True and out["read_only"] is True
    assert out["server_version"].startswith("8.4")
    assert await ready.ok("GET", ready.p("/connections")) == []
    entry = (await ready.audit(action="connection.tested"))[0]
    assert entry["details"]["unsaved"] is True and entry["connection_id"] is None
    wrong = await ready.ok("POST", ready.p("/connections/test"), sources.mysql(password="nope"))
    assert wrong["ok"] is False and "password was refused" in wrong["message"]


@pytest.mark.parametrize("host", ["169.254.169.254", "0xa9fea9fe", "2852039166", "metadata"])
async def test_metadata_hosts_are_refused_at_the_boundary(
    ready: Stack, sources: Sources, host: str
) -> None:
    resp = await ready.client.post(ready.p("/connections"), json=sources.pg(host=host))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


async def test_ac1_the_stored_password_never_follows_the_connection_to_another_server(
    ready: Stack, sources: Sources
) -> None:
    conn = await ready.connection(sources.pg(ssl_mode="prefer"))
    path = ready.p(f"/connections/{conn['id']}")
    for change in (
        {"host": "localhost"},
        {"port": 5999},
        {"username": "someone_else"},
        {"ssl_mode": "disable"},
    ):
        moved = await ready.client.patch(path, json=change)
        assert moved.status_code == 422, (change, moved.text)
        assert moved.json()["error"]["code"] == "password_required"
    # Same values, a stronger TLS mode and the connection's own limits need nothing.
    same = await ready.ok(
        "PATCH", path, {"host": conn["host"], "port": conn["port"], "max_rows": 40}
    )
    assert same["max_rows"] == 40
    stronger = await ready.ok("PATCH", path, {"ssl_mode": "require"})
    assert stronger["ssl_mode"] == "require"
    # With the password typed again, the server may change; the vetted address is used.
    moved = await ready.ok(
        "PATCH", path, {"host": "localhost", "ssl_mode": "disable", "password": READER_PASSWORD}
    )
    assert moved["host"] == "localhost"
    out = await ready.ok("POST", ready.p(f"/connections/{conn['id']}/test"))
    assert out["ok"] is True and out["read_only"] is True, out


async def test_a_host_is_one_name_and_must_resolve(ready: Stack, sources: Sources) -> None:
    # libpq would try each host of a list in turn: the second one was never checked.
    listed = await ready.client.post(
        ready.p("/connections"), json=sources.pg(host="nothing.invalid,169.254.169.254")
    )
    assert listed.status_code == 422 and listed.json()["error"]["code"] == "validation_error"
    out = await ready.ok(
        "POST", ready.p("/connections/test"), sources.pg(host="nothing.invalid", name="x")
    )
    assert out["ok"] is False and "could not be resolved" in out["message"], out


async def test_crud_names_limits_and_ceilings(ready: Stack, sources: Sources) -> None:
    conn = await ready.connection(sources.pg(max_rows=50, max_scan_rows=1000, timeout_s=5))
    assert (conn["max_rows"], conn["max_scan_rows"], conn["timeout_s"]) == (50, 1000, 5)
    assert conn["access"] == {"can_write": True, "via": "role"}
    dup = await ready.client.post(ready.p("/connections"), json=sources.pg())
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "name_taken"
    changed = await ready.ok(
        "PATCH", ready.p(f"/connections/{conn['id']}"), {"name": "Renamed", "max_rows": 25}
    )
    assert changed["name"] == "Renamed" and changed["max_rows"] == 25
    redis_bad = await ready.client.post(
        ready.p("/connections"),
        json={"name": "R", "kind": "redis", "host": "127.0.0.1", "database": "99"},
    )
    assert redis_bad.status_code == 422
    await ready.ok("DELETE", ready.p(f"/connections/{conn['id']}"), status=204)
    gone = await ready.client.get(ready.p(f"/connections/{conn['id']}"))
    assert gone.status_code == 404
    actions = [e["action"] for e in await ready.audit()]
    assert {"connection.created", "connection.updated", "connection.deleted"} <= set(actions)


async def test_unreachable_host_reports_a_clear_message(ready: Stack, sources: Sources) -> None:
    out = await ready.ok("POST", ready.p("/connections/test"), sources.pg(port=1, name="x"))
    assert out["ok"] is False
    assert "Nothing answered" in out["message"] or "timed out" in out["message"]


async def test_sqlite_upload_is_checked(ready: Stack, sources: Sources, tmp_path: object) -> None:
    conn = await ready.sqlite(sources.sqlite_path)
    assert conn["kind"] == "sqlite" and conn["file_size"] > 0
    out = await ready.ok("POST", ready.p(f"/connections/{conn['id']}/test"))
    assert out["ok"] is True and out["read_only"] is True and out["objects"] == 3
    resp = await ready.client.post(
        ready.p("/connections/sqlite"),
        data={"name": "Not a database"},
        files={"file": ("x.sqlite", b"hello world", "application/octet-stream")},
    )
    assert resp.status_code == 400 and resp.json()["error"]["code"] == "not_sqlite"

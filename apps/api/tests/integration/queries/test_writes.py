"""AC5: a human-marked write gets a second, separate confirmation describing exactly what
will run, and is audited (actor, exact statement, connection) before it reaches the
database, then again with the result."""

from __future__ import annotations

from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from tests.integration.conftest import Sources, Stack, source_engine, source_scalar

pytestmark = pytest.mark.integration

NOTES = "SELECT count(*) FROM notes"


async def _prepare(
    stack: Stack, connection_id: str, sql: str, client: Any = None
) -> dict[str, Any]:
    return dict(
        await stack.ok(
            "POST",
            stack.p("/writes"),
            {"connection_id": connection_id, "sql": sql},
            client=client,
            status=201,
        )
    )


async def test_ac5_prepare_runs_nothing_and_confirm_runs_exactly_it_audited_first(
    ready: Stack, sources: Sources
) -> None:
    conn = await ready.connection(sources.pg(admin=True))
    before = source_scalar("postgresql", "shop_test", NOTES)
    sql = "INSERT INTO notes (body) VALUES ('first confirmed write')"
    prepared = await _prepare(ready, conn["id"], sql)
    assert prepared["status"] == "pending" and prepared["kind"] == "write"
    assert prepared["statements"][0]["sql"] == sql
    assert prepared["statements"][0]["summary"] == "Inserts rows into notes"
    assert len(prepared["digest"]) == 64 and prepared["confirm_token"]
    assert source_scalar("postgresql", "shop_test", NOTES) == before  # nothing ran
    shown = await ready.ok("GET", ready.p(f"/confirmations/{prepared['id']}"))
    assert "confirm_token" not in shown  # the token is shown once, never again

    result = await ready.confirm(prepared)
    assert result["confirmation"]["status"] == "executed"
    assert result["confirmation"]["result"]["rows_affected"] == 1
    assert source_scalar("postgresql", "shop_test", NOTES) == before + 1
    run = await ready.ok("GET", ready.p(f"/queries/{result['query_id']}"))
    assert run["mode"] == "write" and run["source"] == "write" and run["rows_affected"] == 1

    entries = await ready.audit(connection_id=conn["id"], action="write.")
    by_action = {e["action"]: e for e in entries}
    confirmed, executed = by_action["write.confirmed"], by_action["write.executed"]
    # The confirmed entry (actor, exact statement, connection) was written first...
    assert confirmed["id"] < executed["id"]
    assert confirmed["actor_email"] == "admin@example.test"
    assert confirmed["details"]["statements"] == [sql]
    assert (
        confirmed["details"]["connection"] == conn["name"]
        and confirmed["connection_id"] == conn["id"]
    )
    # ... and the result followed.
    assert executed["details"]["outcome"] == "ok" and executed["details"]["rows_affected"] == 1
    assert by_action["write.requested"]["id"] < confirmed["id"]
    again = await ready.client.post(
        ready.p(f"/confirmations/{prepared['id']}/confirm"),
        json={"confirm_token": prepared["confirm_token"], "digest": prepared["digest"]},
    )
    assert again.status_code == 409 and again.json()["error"]["code"] == "confirmation_used"


async def test_ac5_the_confirmed_entry_exists_even_when_the_database_refuses_the_write(
    ready: Stack, sources: Sources
) -> None:
    conn = await ready.connection(sources.pg(admin=True))
    prepared = await _prepare(ready, conn["id"], "INSERT INTO notes (body) VALUES (NULL)")
    result = await ready.confirm(prepared)
    assert result["confirmation"]["status"] == "failed"
    assert "null value" in (result["confirmation"]["error"] or "").lower()
    actions = [e["action"] for e in await ready.audit(connection_id=conn["id"], action="write.")]
    assert "write.confirmed" in actions and "write.executed" in actions
    executed = (await ready.audit(connection_id=conn["id"], action="write.executed"))[0]
    assert executed["details"]["outcome"] == "error"


async def test_ac5_wrong_token_wrong_digest_or_another_person_cannot_confirm(
    ready: Stack, sources: Sources
) -> None:
    conn = await ready.connection(sources.pg(admin=True))
    before = source_scalar("postgresql", "shop_test", NOTES)
    prepared = await _prepare(ready, conn["id"], "INSERT INTO notes (body) VALUES ('nope')")
    bad_token = await ready.client.post(
        ready.p(f"/confirmations/{prepared['id']}/confirm"),
        json={"confirm_token": "x" * 32, "digest": prepared["digest"]},
    )
    assert bad_token.status_code == 403 and bad_token.json()["error"]["code"] == "token_mismatch"
    bad_digest = await ready.client.post(
        ready.p(f"/confirmations/{prepared['id']}/confirm"),
        json={"confirm_token": prepared["confirm_token"], "digest": "0" * 64},
    )
    assert (
        bad_digest.status_code == 409 and bad_digest.json()["error"]["code"] == "statement_changed"
    )
    other, _ = await ready.member("admin")
    stolen = await other.post(
        ready.p(f"/confirmations/{prepared['id']}/confirm"),
        json={"confirm_token": prepared["confirm_token"], "digest": prepared["digest"]},
    )
    assert stolen.status_code == 403 and stolen.json()["error"]["code"] == "not_your_confirmation"
    assert source_scalar("postgresql", "shop_test", NOTES) == before
    rejected = await ready.audit(connection_id=conn["id"], action="write.confirmation_rejected")
    assert {e["details"]["reason"] for e in rejected} == {
        "wrong_token",
        "digest_mismatch",
        "different_person",
    }
    cancelled = await ready.ok("POST", ready.p(f"/confirmations/{prepared['id']}/cancel"))
    assert cancelled["status"] == "cancelled"


async def test_ac5_an_expired_confirmation_never_runs(ready: Stack, sources: Sources) -> None:
    conn = await ready.connection(sources.pg(admin=True))
    prepared = await _prepare(ready, conn["id"], "INSERT INTO notes (body) VALUES ('late')")
    engine = create_async_engine(ready.settings.database_url)
    async with engine.begin() as c:
        await c.execute(
            sa.text(
                "UPDATE confirmations SET expires_at = now() - interval '1 minute' WHERE id = :i"
            ),
            {"i": prepared["id"]},
        )
    await engine.dispose()
    resp = await ready.client.post(
        ready.p(f"/confirmations/{prepared['id']}/confirm"),
        json={"confirm_token": prepared["confirm_token"], "digest": prepared["digest"]},
    )
    assert resp.status_code == 410 and resp.json()["error"]["code"] == "confirmation_expired"
    shown = await ready.ok("GET", ready.p(f"/confirmations/{prepared['id']}"))
    assert shown["status"] == "expired"


async def test_ac5_writes_need_a_write_grant(ready: Stack, sources: Sources) -> None:
    conn = await ready.connection(sources.pg(admin=True))
    viewer, viewer_id = await ready.member("viewer")
    await ready.ok(
        "PUT",
        ready.p(f"/connections/{conn['id']}/grants"),
        {"user_id": viewer_id, "can_write": False},
    )
    denied = await viewer.post(
        ready.p("/writes"),
        json={"connection_id": conn["id"], "sql": "INSERT INTO notes (body) VALUES ('v')"},
    )
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "write_not_granted"
    await ready.ok(
        "PUT",
        ready.p(f"/connections/{conn['id']}/grants"),
        {"user_id": viewer_id, "can_write": True},
    )
    prepared = await _prepare(
        ready, conn["id"], "INSERT INTO notes (body) VALUES ('v')", client=viewer
    )
    # The grant is checked again at confirmation time.
    await ready.ok(
        "PUT",
        ready.p(f"/connections/{conn['id']}/grants"),
        {"user_id": viewer_id, "can_write": False},
    )
    late = await viewer.post(
        ready.p(f"/confirmations/{prepared['id']}/confirm"),
        json={"confirm_token": prepared["confirm_token"], "digest": prepared["digest"]},
    )
    assert late.status_code == 403 and late.json()["error"]["code"] == "write_not_granted"


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("BEGIN; DELETE FROM notes; COMMIT", "transaction_control"),
        ("SET search_path = evil", "session_control"),
        ("GRANT ALL ON notes TO public", "privileges"),
        ("VACUUM notes", "unparsed_statement"),
        ("COPY notes TO '/tmp/x'", "file_access"),
    ],
)
async def test_the_write_path_refuses_what_it_cannot_describe(
    ready: Stack, sources: Sources, sql: str, code: str
) -> None:
    conn = await ready.connection(sources.pg(admin=True))
    resp = await ready.client.post(
        ready.p("/writes"), json={"connection_id": conn["id"], "sql": sql}
    )
    assert resp.status_code == 422 and resp.json()["error"]["code"] == code


async def test_a_script_runs_in_one_transaction_and_ddl_refreshes_the_schema(
    ready: Stack, sources: Sources
) -> None:
    conn = await ready.connection(sources.pg(admin=True))
    await ready.ok("GET", ready.p(f"/connections/{conn['id']}/schema"))
    script = (
        "CREATE TABLE dbp_scratch (id serial PRIMARY KEY, label text NOT NULL);\n"
        "INSERT INTO dbp_scratch (label) VALUES ('a'), ('b');\n"
        "DELETE FROM dbp_scratch"
    )
    try:
        prepared = await _prepare(ready, conn["id"], script)
        kinds = [s["kind"] for s in prepared["statements"]]
        assert kinds == ["create", "insert", "delete"]
        assert prepared["statements"][2]["destructive"] is True
        assert any("EVERY row" in w for w in prepared["warnings"])
        result = await ready.confirm(prepared)
        assert [s["rows_affected"] for s in result["confirmation"]["result"]["statements"]] == [
            0,
            2,
            2,
        ]
        schema = await ready.ok("GET", ready.p(f"/connections/{conn['id']}/schema"))
        assert "dbp_scratch" in {o["name"] for o in schema["objects"]}
        failing = await _prepare(
            ready,
            conn["id"],
            "INSERT INTO dbp_scratch (label) VALUES ('kept?');\nINSERT INTO dbp_scratch (label) VALUES (NULL)",
        )
        out = await ready.confirm(failing)
        assert out["confirmation"]["status"] == "failed"
        assert source_scalar("postgresql", "shop_test", "SELECT count(*) FROM dbp_scratch") == 0
    finally:
        engine = source_engine("postgresql", "shop_test")
        with engine.begin() as c:
            c.exec_driver_sql("DROP TABLE IF EXISTS dbp_scratch")
        engine.dispose()


async def test_mysql_writes_work_through_the_same_path(ready: Stack, sources: Sources) -> None:
    conn = await ready.connection(sources.mysql(admin=True))
    before = source_scalar("mysql", "people_test", "SELECT count(*) FROM departments")
    prepared = await _prepare(
        ready, conn["id"], "INSERT INTO departments (name, location) VALUES ('Legal', 'Dublin')"
    )
    result = await ready.confirm(prepared)
    assert result["confirmation"]["status"] == "executed"
    assert source_scalar("mysql", "people_test", "SELECT count(*) FROM departments") == before + 1
    engine = source_engine("mysql", "people_test")
    with engine.begin() as c:
        c.exec_driver_sql("DELETE FROM departments WHERE name = 'Legal'")
    engine.dispose()

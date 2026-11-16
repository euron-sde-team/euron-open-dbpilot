"""AC13: the health card's security checks read the connected engine's own settings through
real queries, and the card is a short cache over a live probe."""

from __future__ import annotations

from typing import Any

import pytest

from tests.integration.conftest import Sources, Stack, source_scalar

pytestmark = pytest.mark.integration


def _check(card: dict[str, Any], check_id: str) -> dict[str, Any]:
    return next(c for c in card["security"] if c["id"] == check_id)


async def test_ac13_postgres_checks_read_the_real_settings(ready: Stack, sources: Sources) -> None:
    admin = await ready.connection(sources.pg(admin=True))
    reader = await ready.connection(sources.pg())
    # The card reads the latest schema snapshot; it never discovers on its own.
    before = await ready.ok("GET", ready.p(f"/connections/{admin['id']}/health"))
    assert before["objects"] is None
    await ready.ok("GET", ready.p(f"/connections/{admin['id']}/schema"))
    card = await ready.ok(
        "GET", ready.p(f"/connections/{admin['id']}/health"), params={"refresh": "true"}
    )
    assert card["reachable"] is True and card["read_only_session"] is True
    real_ssl = source_scalar("postgresql", "shop_test", "SHOW ssl")
    ssl = _check(card, "postgresql.ssl")
    assert ssl["setting"] == f"ssl={real_ssl}"
    assert ssl["status"] == ("pass" if real_ssl == "on" else "warn")
    tls = _check(card, "connection.tls")
    assert tls["status"] == "warn" and "not encrypted" in tls["detail"]
    assert _check(card, "postgresql.password_encryption")[
        "setting"
    ] == "password_encryption=" + source_scalar(
        "postgresql", "shop_test", "SHOW password_encryption"
    )
    assert (
        _check(card, "postgresql.superuser")["status"] == "fail"
    )  # the image's admin is a superuser
    reader_card = await ready.ok("GET", ready.p(f"/connections/{reader['id']}/health"))
    assert _check(reader_card, "postgresql.superuser")["status"] == "pass"
    assert _check(reader_card, "readonly.login")["status"] == "pass"
    assert card["size_bytes"] > 0 and card["active_connections"] >= 1 and card["objects"] == 6
    assert any(
        a["kind"] == "unindexed_foreign_key" and "order_items" in a["table"]
        for a in card["index_advisories"]
    )
    assert card["security_summary"]["failures"] >= 1


async def test_ac13_mysql_checks_read_the_real_settings(ready: Stack, sources: Sources) -> None:
    conn = await ready.connection(sources.mysql(admin=True))
    card = await ready.ok("GET", ready.p(f"/connections/{conn['id']}/health"))
    secure = int(source_scalar("mysql", "people_test", "SELECT @@require_secure_transport"))
    check = _check(card, "mysql.require_secure_transport")
    assert check["setting"] == f"require_secure_transport={'ON' if secure else 'OFF'}"
    assert check["status"] == ("pass" if secure else "warn")
    infile = int(source_scalar("mysql", "people_test", "SELECT @@local_infile"))
    assert (
        _check(card, "mysql.local_infile")["setting"] == f"local_infile={'ON' if infile else 'OFF'}"
    )
    assert _check(card, "mysql.privileges")["status"] == "fail"  # root has ALL PRIVILEGES ON *.*
    reader = await ready.connection(sources.mysql())
    reader_card = await ready.ok("GET", ready.p(f"/connections/{reader['id']}/health"))
    assert _check(reader_card, "mysql.privileges")["status"] == "pass"


async def test_the_card_is_cached_briefly_and_dropped_on_change(
    ready: Stack, sources: Sources
) -> None:
    conn = await ready.connection(sources.pg())
    first = await ready.ok("GET", ready.p(f"/connections/{conn['id']}/health"))
    second = await ready.ok("GET", ready.p(f"/connections/{conn['id']}/health"))
    assert first["cached"] is False and second["cached"] is True
    assert second["checked_at"] == first["checked_at"]
    fresh = await ready.ok(
        "GET", ready.p(f"/connections/{conn['id']}/health"), params={"refresh": "true"}
    )
    assert fresh["cached"] is False
    await ready.ok("PATCH", ready.p(f"/connections/{conn['id']}"), {"description": "changed"})
    assert (await ready.ok("GET", ready.p(f"/connections/{conn['id']}/health")))["cached"] is False
    probes = await ready.audit(action="connection.health_checked", connection_id=conn["id"])
    assert len(probes) == 3  # one per live probe, none for the cached read


async def test_an_unreachable_database_is_reported_not_raised(
    ready: Stack, sources: Sources
) -> None:
    conn = await ready.connection(sources.pg(port=1, name="Down", timeout_s=3))
    card = await ready.ok("GET", ready.p(f"/connections/{conn['id']}/health"))
    assert card["reachable"] is False and "Nothing answered" in card["error"]

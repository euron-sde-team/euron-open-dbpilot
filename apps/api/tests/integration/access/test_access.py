"""AC9: a project member without a grant on a connection cannot see it, its schema, or run
anything against it through any route: 404, exactly like a connection that does not exist.
Everything derived from it is filtered the same way."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from tests.integration.conftest import Sources, Stack

pytestmark = pytest.mark.integration


async def _setup(
    ready: Stack, sources: Sources
) -> tuple[dict[str, Any], dict[str, Any], httpx.AsyncClient, str]:
    granted = await ready.connection(sources.pg(name="Granted"))
    hidden = await ready.connection(sources.mysql(name="Hidden"))
    viewer, viewer_id = await ready.member("viewer")
    await ready.ok("PUT", ready.p(f"/connections/{granted['id']}/grants"), {"user_id": viewer_id})
    # Some history on the hidden connection, made by the admin.
    await ready.ok("GET", ready.p(f"/connections/{hidden['id']}/schema"))
    await ready.sql(hidden["id"], "SELECT count(*) FROM employees")
    gen = await ready.ok(
        "POST",
        ready.p("/ai/sql"),
        {"connection_id": hidden["id"], "question": "how many employees"},
    )
    await ready.ok("POST", ready.p("/backups"), {"connection_id": hidden["id"]}, status=201)
    return granted, hidden, viewer, gen["id"]


async def test_ac9_every_route_answers_404_for_an_ungranted_connection(
    ready: Stack, sources: Sources
) -> None:
    granted, hidden, viewer, gen_id = await _setup(ready, sources)
    cid = hidden["id"]
    probes: list[tuple[str, str, Any]] = [
        ("GET", f"/connections/{cid}", None),
        ("POST", f"/connections/{cid}/test", None),
        ("GET", f"/connections/{cid}/schema", None),
        ("POST", f"/connections/{cid}/schema/refresh", None),
        ("GET", f"/connections/{cid}/schema/snapshots", None),
        ("GET", f"/connections/{cid}/schema/search?q=emp", None),
        ("GET", f"/connections/{cid}/schema/relationships", None),
        ("GET", f"/connections/{cid}/health", None),
        ("POST", f"/connections/{cid}/preview", {"object": "employees"}),
        ("POST", "/queries/sql", {"connection_id": cid, "sql": "SELECT 1"}),
        ("POST", "/queries/validate", {"connection_id": cid, "sql": "SELECT 1"}),
        ("POST", "/ai/sql", {"connection_id": cid, "question": "x"}),
        ("POST", "/ai/explain", {"connection_id": cid, "sql": "SELECT 1"}),
        ("POST", "/ai/index-suggestions", {"connection_id": cid}),
        ("POST", "/ai/diagnose", {"connection_id": cid, "sql": "x", "error": "y"}),
        ("GET", f"/ai/generations/{gen_id}", None),
        ("POST", f"/ai/generations/{gen_id}/run", None),
        ("POST", "/writes", {"connection_id": cid, "sql": "DELETE FROM employees"}),
        ("POST", "/saved-queries", {"connection_id": cid, "name": "x", "statement": "SELECT 1"}),
        ("POST", "/comparisons", {"from_connection_id": granted["id"], "to_connection_id": cid}),
    ]
    for method, path, body in probes:
        resp = await viewer.request(method, ready.p(path), json=body)
        assert resp.status_code == 404, f"{method} {path}: {resp.status_code} {resp.text}"
        assert resp.json()["error"]["code"] in ("not_found", "unknown_object")
    # A connection id that does not exist at all answers the same way.
    ghost = await viewer.get(ready.p("/connections/00000000-0000-0000-0000-000000000000"))
    assert (
        ghost.status_code == 404
        and ghost.json() == (await viewer.get(ready.p(f"/connections/{cid}"))).json()
    )


async def test_ac9_lists_and_derived_objects_hide_the_ungranted_connection(
    ready: Stack, sources: Sources
) -> None:
    granted, hidden, viewer, _ = await _setup(ready, sources)
    await ready.sql(granted["id"], "SELECT 1")
    listed = (await viewer.get(ready.p("/connections"))).json()
    assert [c["id"] for c in listed] == [granted["id"]]
    assert listed[0]["access"] == {"can_write": False, "via": "grant"}
    for path in ("/queries", "/ai/generations", "/backups", "/confirmations", "/comparisons"):
        items = (await viewer.get(ready.p(path))).json()["items"]
        assert all(i.get("connection_id") != hidden["id"] for i in items), path
    project = (await viewer.get(ready.p(""))).json()
    assert project["connections"] == 1 and project["role"] == "viewer"
    overview = (await viewer.get(ready.p("/overview"))).json()["totals"]
    assert overview["connections"] == 1 and overview["connections_by_engine"] == {"postgresql": 1}
    # The admin still sees both.
    assert len(await ready.ok("GET", ready.p("/connections"))) == 2


async def test_grants_to_every_member_and_revoking(ready: Stack, sources: Sources) -> None:
    conn = await ready.connection(sources.pg())
    viewer, _ = await ready.member("viewer")
    assert (await viewer.get(ready.p(f"/connections/{conn['id']}"))).status_code == 404
    grants = await ready.ok("PUT", ready.p(f"/connections/{conn['id']}/grants"), {"user_id": None})
    assert grants[0]["all_members"] is True
    shown = (await viewer.get(ready.p(f"/connections/{conn['id']}"))).json()
    assert shown["access"]["via"] == "all_members"
    await ready.ok(
        "DELETE", ready.p(f"/connections/{conn['id']}/grants/{grants[0]['id']}"), status=204
    )
    assert (await viewer.get(ready.p(f"/connections/{conn['id']}"))).status_code == 404


async def test_people_outside_the_project_or_org_see_nothing(
    ready: Stack, sources: Sources
) -> None:
    conn = await ready.connection(sources.pg())
    outsider, _ = await ready.user_in_org("member")  # in the org, not in the project
    for path in ("", "/connections", f"/connections/{conn['id']}"):
        assert (await outsider.get(ready.p(path))).status_code == 404
    viewer, _ = await ready.member("viewer")
    for method, path in (("POST", "/connections"), ("GET", "/audit"), ("POST", "/backups")):
        resp = await viewer.request(method, ready.p(path), json={})
        assert resp.status_code in (403, 422), path
    anon = httpx.AsyncClient(base_url=ready.api_url)
    resp = await anon.get(ready.p("/connections"))
    assert resp.status_code == 401 and resp.json()["error"]["code"] == "not_signed_in"
    await anon.aclose()


async def test_cookie_sessions_need_the_csrf_header_on_changes(ready: Stack) -> None:
    async with httpx.AsyncClient(base_url=ready.api_url) as browser:
        login = await browser.post(
            "/api/v1/auth/login", json={"email": "admin@example.test", "password": "Admin-Pass-123"}
        )
        assert login.status_code == 200 and "dbp_session" in login.cookies
        assert (await browser.get(ready.p(""))).status_code == 200
        blocked = await browser.patch(ready.p(""), json={"description": "x"})
        assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "csrf_required"
        allowed = await browser.patch(
            ready.p(""), json={"description": "x"}, headers={"x-dbp-csrf": "1"}
        )
        assert allowed.status_code == 200
        await browser.post("/api/v1/auth/logout", headers={"x-dbp-csrf": "1"})
        token = login.json()["session_token"]
        after = await browser.get("/api/v1/auth/me", headers={"authorization": f"Bearer {token}"})
        assert after.status_code == 401  # signed out server side

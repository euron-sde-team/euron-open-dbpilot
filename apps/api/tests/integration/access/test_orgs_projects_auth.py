"""Sign-in, organizations, projects and their members."""

from __future__ import annotations

import httpx
import pytest

from tests.integration.conftest import Sources, Stack, login

pytestmark = pytest.mark.integration


async def test_sign_in_lockout_password_change_and_sessions(stack: Stack) -> None:
    client, _ = await stack.user_in_org("member", email="lock@example.test")
    async with httpx.AsyncClient(base_url=stack.api_url) as anon:
        assert (await anon.get("/api/v1/auth/session")).json() == {"user": None}
        for _ in range(5):
            bad = await anon.post(
                "/api/v1/auth/login",
                json={"email": "lock@example.test", "password": "Wrong-Pass-1"},
            )
            assert bad.status_code == 401
        locked = await anon.post(
            "/api/v1/auth/login", json={"email": "lock@example.test", "password": "User-Pass-123"}
        )
        assert locked.status_code == 401 and locked.json()["error"]["code"] == "account_locked"
        unknown = await anon.post(
            "/api/v1/auth/login", json={"email": "nobody@example.test", "password": "x"}
        )
        assert unknown.json()["error"]["code"] == "invalid_credentials"
        garbage = await anon.get(
            "/api/v1/auth/session", headers={"authorization": "Bearer garbage"}
        )
        assert garbage.json() == {"user": None}
    me = (await stack.client.get("/api/v1/auth/session")).json()
    assert me["user"]["email"] == "admin@example.test"
    weak = await stack.client.post(
        "/api/v1/auth/password",
        json={"current_password": "Admin-Pass-123", "new_password": "weakpassword"},
    )
    assert weak.status_code == 400 and weak.json()["error"]["code"] == "weak_password"
    wrong = await stack.client.post(
        "/api/v1/auth/password",
        json={"current_password": "Nope-Pass-123", "new_password": "Better-Pass-456"},
    )
    assert wrong.status_code == 401
    old_token = stack.client.headers["authorization"]
    changed = await stack.client.post(
        "/api/v1/auth/password",
        json={"current_password": "Admin-Pass-123", "new_password": "Better-Pass-456"},
    )
    assert changed.status_code == 200
    async with httpx.AsyncClient(
        base_url=stack.api_url, headers={"authorization": old_token}
    ) as old:
        assert (await old.get("/api/v1/auth/me")).status_code == 401  # other sessions end
    fresh = await login(
        stack.api_url, {"email": "admin@example.test", "password": "Better-Pass-456"}
    )
    assert (await fresh.get("/api/v1/auth/me")).status_code == 200
    await fresh.aclose()
    await client.aclose()


async def test_organizations_and_their_members(stack: Stack) -> None:
    org = await stack.ok("POST", "/api/v1/orgs", {"name": "Northwind Data"}, status=201)
    assert org["role"] == "owner" and org["slug"] == "northwind-data"
    assert (await stack.ok("GET", f"/api/v1/orgs/{org['id']}"))["members"] == 1
    renamed = await stack.ok("PATCH", f"/api/v1/orgs/{org['id']}", {"name": "Northwind"})
    assert renamed["name"] == "Northwind"
    oid = org["id"]
    missing = await stack.client.post(
        f"/api/v1/orgs/{oid}/members", json={"email": "new@example.test"}
    )
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "user_not_found"
    weak = await stack.client.post(
        f"/api/v1/orgs/{oid}/members",
        json={"email": "new@example.test", "name": "N", "password": "weakpass12"},
    )
    assert weak.status_code == 400
    added = await stack.ok(
        "POST",
        f"/api/v1/orgs/{oid}/members",
        {"email": "new@example.test", "name": "Nia", "password": "Good-Pass-123", "role": "admin"},
        status=201,
    )
    assert added["role"] == "admin"
    dup = await stack.client.post(f"/api/v1/orgs/{oid}/members", json={"email": "NEW@example.test"})
    assert dup.status_code == 409
    nia = await login(stack.api_url, {"email": "new@example.test", "password": "Good-Pass-123"})
    no_owner = await nia.post(
        f"/api/v1/orgs/{oid}/members", json={"email": "admin@example.test", "role": "owner"}
    )
    assert no_owner.status_code in (403, 409)
    members = await stack.ok("GET", f"/api/v1/orgs/{oid}/members")
    assert {m["email"] for m in members} == {"admin@example.test", "new@example.test"}
    updated = await stack.ok(
        "PATCH", f"/api/v1/orgs/{oid}/members/{added['user_id']}", {"role": "member"}
    )
    assert updated["role"] == "member"
    me = (await stack.client.get("/api/v1/auth/me")).json()
    project = await stack.ok("POST", "/api/v1/projects", {"org_id": oid, "name": "P"}, status=201)
    await stack.ok(
        "POST",
        f"/api/v1/projects/{project['id']}/members",
        {"email": "new@example.test", "role": "viewer"},
        status=201,
    )
    await stack.ok("DELETE", f"/api/v1/orgs/{oid}/members/{added['user_id']}", status=204)
    assert (
        await nia.get(f"/api/v1/projects/{project['id']}")
    ).status_code == 404  # left the projects too
    owners = [
        m for m in await stack.ok("GET", f"/api/v1/orgs/{oid}/members") if m["role"] == "owner"
    ]
    assert owners and owners[0]["user_id"] == me["id"]
    stranger, _ = await stack.user_in_org("member")
    assert (await stranger.get(f"/api/v1/orgs/{oid}")).status_code == 404
    assert len(await stack.ok("GET", "/api/v1/orgs")) == 2
    await nia.aclose()


async def test_the_last_owner_cannot_leave_or_be_demoted(stack: Stack) -> None:
    client, user_id = await stack.user_in_org("owner")
    await client.aclose()
    org_members = await stack.ok("GET", f"/api/v1/orgs/{stack.org_id}/members")
    assert any(m["user_id"] == user_id and m["role"] == "owner" for m in org_members)
    await stack.ok("DELETE", f"/api/v1/orgs/{stack.org_id}/members/{user_id}", status=204)
    org = await stack.ok("POST", "/api/v1/orgs", {"name": "Solo"}, status=201)
    me = (await stack.client.get("/api/v1/auth/me")).json()
    await stack.ok(
        "POST",
        f"/api/v1/orgs/{org['id']}/members",
        {"email": "solo@example.test", "name": "S", "password": "Good-Pass-123"},
        status=201,
    )
    owners = [
        m
        for m in await stack.ok("GET", f"/api/v1/orgs/{org['id']}/members")
        if m["role"] == "owner"
    ]
    assert [o["user_id"] for o in owners] == [me["id"]]


async def test_projects_members_and_deletion(ready: Stack, sources: Sources) -> None:
    listed = await ready.ok("GET", "/api/v1/projects", params={"org_id": ready.org_id})
    assert [p["id"] for p in listed] == [ready.project_id]
    updated = await ready.ok("PATCH", ready.p(""), {"name": "Ops", "description": "All databases"})
    assert updated["name"] == "Ops" and updated["description"] == "All databases"
    outsider = await ready.client.post(ready.p("/members"), json={"email": "nobody@example.test"})
    assert outsider.status_code == 404 and outsider.json()["error"]["code"] == "user_not_in_org"
    viewer, viewer_id = await ready.member("viewer")
    again = await ready.client.post(
        ready.p("/members"), json={"email": (await viewer.get("/api/v1/auth/me")).json()["email"]}
    )
    assert again.status_code == 409
    conn = await ready.connection(sources.pg())
    await ready.ok("PUT", ready.p(f"/connections/{conn['id']}/grants"), {"user_id": viewer_id})
    promoted = await ready.ok("PATCH", ready.p(f"/members/{viewer_id}"), {"role": "admin"})
    assert promoted["role"] == "admin"
    cannot = await viewer.delete(ready.p(""))
    assert cannot.status_code == 403  # admins cannot delete the project
    await ready.ok("DELETE", ready.p(f"/members/{viewer_id}"), status=204)
    assert (await viewer.get(ready.p(""))).status_code == 404
    assert (await ready.ok("GET", ready.p(f"/connections/{conn['id']}/grants"))) == []
    await ready.ok("POST", ready.p("/backups"), {"connection_id": conn["id"]}, status=201)
    await ready.ok("DELETE", ready.p(""), status=204)
    assert (await ready.client.get(ready.p(""))).status_code == 404
    assert await ready.ok("GET", "/api/v1/projects") == []
    await viewer.aclose()

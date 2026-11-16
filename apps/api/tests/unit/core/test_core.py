"""Core building blocks: the production boot guard, the cache, sealing, storage, host checks,
security helpers, middleware and the single error shape."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from dbpilot.core import kv as kvmod
from dbpilot.core.config import Settings
from dbpilot.core.errors import AppError, NotFoundError, install_error_handlers
from dbpilot.core.kv import MemoryKV
from dbpilot.core.middleware import BodySizeLimitMiddleware, RequestContextMiddleware
from dbpilot.core.net import (
    HostRefusedError,
    as_ip,
    canonical_host,
    check_host,
    is_forbidden_ip,
    resolves_safely,
    vet_host,
)
from dbpilot.core.schemas import decode_cursor, encode_cursor
from dbpilot.core.sealing import LocalSealer
from dbpilot.core.security import (
    decode_session,
    hash_password,
    issue_session,
    new_confirmation_token,
    password_problems,
    token_hash,
    verify_password,
)
from dbpilot.core.storage import LocalStorage, ObjectNotFoundError

GOOD = {
    "environment": "production",
    "session_secret": "a" * 40,
    "local_master_key": "b" * 40,
    "cookie_secure": True,
    "storage": "s3",
    "ai_provider": "none",
    "public_url": "https://dbpilot.example.com",
    "cors_origins": "https://dbpilot.example.com",
}


def test_production_boot_guard_refuses_unsafe_config() -> None:
    Settings(**GOOD).guard()  # a safe production config boots
    with pytest.raises(RuntimeError) as err:
        Settings(environment="production").guard()
    text = str(err.value)
    for needle in (
        "SESSION_SECRET",
        "LOCAL_MASTER_KEY",
        "COOKIE_SECURE",
        "STORAGE must be s3",
        "AI_PROVIDER=mock",
        "PUBLIC_URL",
    ):
        assert needle in text
    with pytest.raises(RuntimeError, match="must differ"):
        Settings(**{**GOOD, "local_master_key": "a" * 40}).guard()
    bad_ai = Settings(**{**GOOD, "ai_provider": "openai"}).problems()
    assert any("AI_API_KEY" in p for p in bad_ai) and any("AI_MODEL" in p for p in bad_ai)
    assert any("never *" in p for p in Settings(**{**GOOD, "cors_origins": "*"}).problems())
    assert any(
        "AWS_ENDPOINT_URL" in p
        for p in Settings(
            **{**GOOD, "aws_endpoint_url": "http://x", "DBPILOT_CLOUD": "aws"}
        ).problems()
    )
    Settings(environment="development").guard()  # development never refuses


def test_comma_separated_origins_are_split() -> None:
    s = Settings(cors_origins="http://a.test, http://b.test")
    assert s.cors_origins == ["http://a.test", "http://b.test"]
    assert Settings(redis_url="").cache_backend == "memory"


async def test_memory_kv_expires_evicts_and_limits() -> None:
    kv = MemoryKV(max_entries=2, max_bytes=1_000)
    await kv.set("a", b"1", 60)
    await kv.set("b", b"2", 60)
    await kv.set("c", b"3", 60)
    assert await kv.get("a") is None and await kv.get("c") == b"3"
    await kv.set("big", b"x" * 2_000, 60)
    assert await kv.get("big") is None
    await kv.set("short", b"1", 1)
    kv._data["short"] = (0.0, b"1")
    assert await kv.get("short") is None
    await kv.delete("c")
    assert await kv.get("c") is None
    decisions = [await kv.hit("login:x", 2) for _ in range(3)]
    assert [d.allowed for d in decisions] == [True, True, False] and decisions[2].retry_after_s >= 1
    await kv.ping()
    await kv.close()


async def test_cache_helpers_treat_outages_as_misses() -> None:
    class Broken:
        backend = "redis"

        async def get(self, key: str) -> bytes | None:
            raise OSError("down")

        async def set(self, key: str, value: bytes, ttl_s: int) -> None:
            raise OSError("down")

        async def delete(self, key: str) -> None:
            raise OSError("down")

        async def hit(self, name: str, limit: int, amount: int = 1) -> Any:
            raise OSError("down")

    broken: Any = Broken()
    assert await kvmod.cache_get(broken, "k", "t") is None
    await kvmod.cache_set(broken, "k", b"v", 10, "t")
    await kvmod.cache_delete(broken, "k", "t")
    assert (await kvmod.limit(broken, "x", 1)).allowed is True
    memory = MemoryKV()
    await kvmod.cache_set(memory, "k", b"v", 0, "t")  # a zero TTL caches nothing
    assert await memory.get("k") is None


async def test_local_sealing_binds_the_record() -> None:
    sealer = LocalSealer("test-only-master-key-with-32-plus-bytes-ok")
    sealed = await sealer.seal("hunter2", "connection:1:password")
    assert b"hunter2" not in sealed
    assert await sealer.open(sealed, "connection:1:password") == "hunter2"
    with pytest.raises(Exception):  # noqa: B017 another record's context fails
        await sealer.open(sealed, "connection:2:password")
    with pytest.raises(ValueError):
        await sealer.open(b"\x09" + sealed[1:], "connection:1:password")


async def test_local_storage_round_trips_and_stays_inside_its_root(tmp_path: Path) -> None:
    storage = LocalStorage(str(tmp_path / "root"))
    await storage.put("a/b.txt", b"hello", "text/plain")
    assert await storage.get("a/b.txt") == b"hello"
    src = tmp_path / "src.bin"
    src.write_bytes(b"data")
    await storage.put_file("f.bin", src, "application/octet-stream")
    dest = tmp_path / "dest.bin"
    await storage.get_file("f.bin", dest)
    assert dest.read_bytes() == b"data"
    await storage.delete("f.bin")
    with pytest.raises(ObjectNotFoundError):
        await storage.get("f.bin")
    with pytest.raises(ObjectNotFoundError):
        await storage.get_file("f.bin", dest)
    with pytest.raises(ValueError):
        await storage.put("../escape", b"x", "text/plain")


def test_host_checks_canonicalize_and_refuse_metadata() -> None:
    assert canonical_host("0xa9fea9fe") == "169.254.169.254"
    assert canonical_host("2852039166") == "169.254.169.254"
    assert canonical_host("[::1]") == "::1"
    unspecified = ".".join(["0"] * 4)  # the wildcard address, refused as a host
    for bad in ("169.254.169.254", "0xa9fea9fe", "metadata", "http://x", "a b", unspecified):
        with pytest.raises(ValueError):
            check_host(bad)
    assert check_host(" DB.Example.COM. ") == "db.example.com"
    ip = as_ip("fd00:ec2::254")
    assert ip is not None and is_forbidden_ip(ip)
    assert as_ip("not-an-ip") is None


async def test_resolution_checks() -> None:
    assert await resolves_safely("127.0.0.1", 5432) is True
    assert await resolves_safely("127.0.0.1", 5432, allow_private=False) is False
    assert await resolves_safely("metadata", 80) is False
    assert await resolves_safely("", 80) is False


def test_a_host_is_one_name_or_one_address() -> None:
    # libpq would try every host of a comma-separated list, the second one unchecked.
    for bad in (
        "nothing.invalid,169.254.169.254",
        "a;b",
        "db.example.com:5432",
        "-x.example",
        "a..b",
    ):
        with pytest.raises(ValueError):
            check_host(bad)
    assert check_host("source_postgres") == "source_postgres"
    assert check_host("::1") == "::1"
    # An IPv4-mapped IPv6 address is judged as the IPv4 address it reaches.
    with pytest.raises(ValueError):
        check_host("::ffff:169.254.169.254")
    mapped = as_ip("::ffff:10.0.0.1")
    assert mapped is not None and mapped.version == 4


async def test_vetting_returns_the_address_and_refuses_what_does_not_resolve() -> None:
    assert await vet_host("127.0.0.1", 5432) == "127.0.0.1"
    assert await vet_host("localhost", 5432) in ("127.0.0.1", "::1")
    with pytest.raises(HostRefusedError) as unresolved:
        await vet_host("nothing.invalid", 5432)
    assert unresolved.value.code == "host_unresolved"
    with pytest.raises(HostRefusedError) as listed:
        await vet_host("nothing.invalid,169.254.169.254", 5432)
    assert listed.value.code == "host_refused"
    with pytest.raises(HostRefusedError) as private:
        await vet_host("localhost", 5432, allow_private=False)
    assert "private" in private.value.message


def test_security_helpers() -> None:
    h = hash_password("Correct-Horse-1")
    assert verify_password("Correct-Horse-1", h) and not verify_password("wrong", h)
    assert not verify_password("x", None) and not verify_password("x", "not-a-hash")
    assert password_problems("short") == [
        "at least 10 characters",
        "upper and lower case letters",
        "a digit",
    ]
    assert password_problems("Long-Enough-1") == []
    import uuid

    uid = uuid.uuid4()
    token = issue_session(uid, "user", 3, "s" * 40, 1)
    claims = decode_session(token, "s" * 40)
    assert claims is not None and claims.user_id == uid and claims.session_version == 3
    assert (
        decode_session(token, "other" * 10) is None and decode_session("garbage", "s" * 40) is None
    )
    plain, hashed = new_confirmation_token()
    assert token_hash(plain) == hashed and plain not in hashed
    assert decode_cursor(encode_cursor("2026|x")) == "2026|x" and not decode_cursor("@@@")
    assert decode_cursor(None) is None


def _app(settings: Settings) -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/api/boom")
    async def boom() -> None:
        raise RuntimeError("secret detail")

    @app.get("/api/missing")
    async def missing() -> None:
        raise NotFoundError("Thing not found", details={"id": 1})

    @app.post("/api/echo")
    async def echo(body: dict[str, Any]) -> dict[str, Any]:
        return body

    @app.get("/api/app-error")
    async def app_error() -> None:
        raise AppError("Plain", code="plain", headers={"x-extra": "1"})

    app.add_middleware(BodySizeLimitMiddleware, settings=settings)
    app.add_middleware(RequestContextMiddleware)
    return app


async def test_middleware_and_error_shape() -> None:
    app = _app(Settings(max_body_bytes=10_000))
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        boom = await client.get("/api/boom")
        assert boom.status_code == 500
        assert boom.json() == {
            "error": {"code": "internal_error", "message": "Internal server error", "details": {}}
        }
        assert "secret detail" not in boom.text
        missing = await client.get("/api/missing", headers={"x-request-id": "abc-123"})
        assert missing.status_code == 404 and missing.json()["error"]["details"] == {"id": 1}
        assert missing.headers["x-request-id"] == "abc-123"
        assert (
            missing.headers["cache-control"] == "no-store"
            and missing.headers["x-frame-options"] == "DENY"
        )
        assert (await client.get("/api/app-error")).headers["x-extra"] == "1"
        bad = await client.post(
            "/api/echo", content=b"not json", headers={"content-type": "application/json"}
        )
        assert bad.status_code == 422 and bad.json()["error"]["code"] == "validation_error"
        big = await client.post(
            "/api/echo", content=b"x" * 20_000, headers={"content-type": "application/json"}
        )
        assert big.status_code == 413
        ok = await client.post("/api/echo", json={"a": 1})
        assert ok.json() == {"a": 1} and len(ok.headers["x-request-id"]) == 16
        assert (await client.get("/nowhere")).json()["error"]["code"] == "not_found"


async def test_chunked_bodies_are_counted_as_they_arrive() -> None:
    app = _app(Settings(max_body_bytes=10_000))
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)

    async def chunks() -> Any:
        for _ in range(5):
            yield b"x" * 4_000
            await asyncio.sleep(0)

    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.post(
            "/api/echo", content=chunks(), headers={"content-type": "application/json"}
        )
    assert resp.status_code == 413

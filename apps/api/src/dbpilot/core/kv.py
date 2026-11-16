"""A small key-value interface with two backends: Redis, or process memory.

DBPilot must run with no Redis at all, so everything that would use Redis (the schema
snapshot cache, the health and dashboard response caches, short-lived query results and
rate limits) goes through :class:`KV`. With ``REDIS_URL`` set, state is shared by every
replica; without it each process keeps its own, which is exact for a single replica.

Every key starts with ``dbp:`` so a Redis shared with other applications never collides.
A Redis outage never stops the API: callers treat cache errors as a miss and count them in
``dbpilot_dependency_errors_total``.
"""

from __future__ import annotations

import math
import time
from collections import OrderedDict
from typing import Protocol

from redis.asyncio import Redis

from dbpilot.core.limiter import LimitDecision, Limiter

PREFIX = "dbp:"


class KV(Protocol):
    backend: str

    async def get(self, key: str) -> bytes | None: ...
    async def set(self, key: str, value: bytes, ttl_s: int) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def hit(self, name: str, limit: int, amount: int = 1) -> LimitDecision: ...
    async def ping(self) -> None: ...
    async def close(self) -> None: ...


class RedisKV:
    backend = "redis"

    def __init__(self, url: str) -> None:
        self.redis = Redis.from_url(
            url,
            decode_responses=False,
            socket_timeout=2.0,
            socket_connect_timeout=2.0,
            health_check_interval=15,
            retry_on_timeout=False,
        )
        self._limiter = Limiter(self.redis)

    async def get(self, key: str) -> bytes | None:
        value = await self.redis.get(PREFIX + key)
        if value is None:
            return None
        return value if isinstance(value, bytes) else str(value).encode()

    async def set(self, key: str, value: bytes, ttl_s: int) -> None:
        await self.redis.set(PREFIX + key, value, ex=max(int(ttl_s), 1))

    async def delete(self, key: str) -> None:
        await self.redis.delete(PREFIX + key)

    async def hit(self, name: str, limit: int, amount: int = 1) -> LimitDecision:
        return await self._limiter.hit(name, limit, amount)

    async def ping(self) -> None:
        await self.redis.ping()

    async def close(self) -> None:
        await self.redis.aclose()


class MemoryKV:
    """LRU with expiry, bounded by entries and bytes, plus fixed-window rate counters.

    One process only. The byte budget matters: a cached schema or a stored result can be
    megabytes, so an entry count alone would let the cache grow to gigabytes.
    """

    backend = "memory"

    def __init__(self, max_entries: int = 4096, max_bytes: int = 64_000_000) -> None:
        self._data: OrderedDict[str, tuple[float, bytes]] = OrderedDict()
        self._windows: dict[str, tuple[int, int]] = {}
        self._max = max_entries
        self._max_bytes = max_bytes
        self._bytes = 0

    async def get(self, key: str) -> bytes | None:
        hit = self._data.get(key)
        if hit is None:
            return None
        if hit[0] < time.monotonic():
            self._drop(key)
            return None
        self._data.move_to_end(key)
        return hit[1]

    async def set(self, key: str, value: bytes, ttl_s: int) -> None:
        self._drop(key)
        if len(value) > self._max_bytes:
            return
        self._data[key] = (time.monotonic() + max(int(ttl_s), 1), value)
        self._bytes += len(key) + len(value)
        while len(self._data) > self._max or self._bytes > self._max_bytes:
            self._drop(next(iter(self._data)))

    async def delete(self, key: str) -> None:
        self._drop(key)

    def _drop(self, key: str) -> None:
        old = self._data.pop(key, None)
        if old is not None:
            self._bytes -= len(key) + len(old[1])

    async def hit(self, name: str, limit: int, amount: int = 1) -> LimitDecision:
        now = time.time()
        window = int(now // Limiter.WINDOW_S)
        if len(self._windows) > 10_000:
            # Only the current window matters; forget every key from earlier ones.
            self._windows = {k: v for k, v in self._windows.items() if v[0] == window}
        current_window, count = self._windows.get(name, (window, 0))
        if current_window != window:
            count = 0
        if count + amount > limit:
            retry = math.ceil(Limiter.WINDOW_S - (now % Limiter.WINDOW_S))
            return LimitDecision(False, max(1, retry), count)
        self._windows[name] = (window, count + amount)
        return LimitDecision(True, 0, count + amount)

    async def ping(self) -> None:
        return None

    async def close(self) -> None:
        self._data.clear()
        self._bytes = 0


def create_kv(redis_url: str, memory_max_bytes: int = 64_000_000) -> KV:
    return RedisKV(redis_url) if redis_url else MemoryKV(max_bytes=memory_max_bytes)


async def limit(kv: KV, name: str, rpm: int) -> LimitDecision:
    """A rate-limit hit that lets the request through when Redis is down (counted in
    ``dbpilot_dependency_errors_total``): throttling is protection, not availability."""
    from dbpilot.core import metrics

    try:
        return await kv.hit(name, rpm)
    except Exception:
        metrics.DEPENDENCY_ERRORS.labels("cache", "rate_limit").inc()
        return LimitDecision(allowed=True)


async def cache_get(kv: KV, key: str, cache: str) -> bytes | None:
    """A cache read that treats an outage as a miss, and counts hits and misses."""
    from dbpilot.core import metrics

    try:
        value = await kv.get(key)
    except Exception:
        metrics.DEPENDENCY_ERRORS.labels("cache", f"{cache}_read").inc()
        metrics.CACHE.labels(cache, "error").inc()
        return None
    metrics.CACHE.labels(cache, "hit" if value is not None else "miss").inc()
    return value


async def cache_set(kv: KV, key: str, value: bytes, ttl_s: int, cache: str) -> None:
    from dbpilot.core import metrics

    if ttl_s <= 0:
        return
    try:
        await kv.set(key, value, ttl_s)
    except Exception:
        metrics.DEPENDENCY_ERRORS.labels("cache", f"{cache}_write").inc()


async def cache_delete(kv: KV, key: str, cache: str) -> None:
    from dbpilot.core import metrics

    try:
        await kv.delete(key)
    except Exception:
        metrics.DEPENDENCY_ERRORS.labels("cache", f"{cache}_delete").inc()

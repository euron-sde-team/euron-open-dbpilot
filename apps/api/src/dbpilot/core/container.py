"""Process-wide services, built once in the app lifespan and shared by every request.

Holding them in one object (on ``app.state``) instead of module globals keeps the app
factory re-entrant and lets tests build an isolated container per app.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from dbpilot.core.aws import aws_client
from dbpilot.core.config import Settings
from dbpilot.core.db import Database
from dbpilot.core.kv import KV, create_kv
from dbpilot.core.sealing import KeySealer, KmsSealer, LocalSealer
from dbpilot.core.storage import LocalStorage, ObjectStorage, S3Storage


def build_http_client(timeout_s: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_s, connect=5.0),
        limits=httpx.Limits(max_connections=50, max_keepalive_connections=10),
        follow_redirects=False,
    )


@dataclass
class Container:
    settings: Settings
    db: Database
    kv: KV
    sealer: KeySealer
    storage: ObjectStorage
    # Outbound calls to the configured model provider.
    http: httpx.AsyncClient

    @classmethod
    def build(cls, settings: Settings) -> Container:
        sealer: KeySealer = (
            KmsSealer(aws_client(settings, "kms"), settings.kms_key_id)
            if settings.key_mode == "kms"
            else LocalSealer(settings.local_master_key)
        )
        storage: ObjectStorage = (
            S3Storage(aws_client(settings, "s3"), settings.objects_bucket)
            if settings.storage == "s3"
            else LocalStorage(settings.local_storage_dir)
        )
        return cls(
            settings=settings,
            db=Database(
                settings.database_url,
                pool_size=settings.db_pool_size,
                max_overflow=settings.db_max_overflow,
                pool_timeout_s=settings.db_pool_timeout_s,
            ),
            kv=create_kv(settings.redis_url, settings.memory_cache_max_bytes),
            sealer=sealer,
            storage=storage,
            http=build_http_client(settings.ai_timeout_s),
        )

    async def close(self) -> None:
        await self.http.aclose()
        await self.kv.close()
        await self.db.dispose()

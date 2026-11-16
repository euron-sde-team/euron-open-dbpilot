"""Runtime configuration, validated once at boot.

Every setting comes from the environment and is read here only. The API, the scheduled
backup worker and the CLI share one ``Settings``. Security-relevant settings are checked by
``guard()``, so a misconfigured production deployment refuses to start instead of running
with demo secrets, local disk for shared backups, or the deterministic mock model.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

INSECURE_SECRETS = {"", "change-me", "replace-with-output-of-make-env", "dev", "secret"}

Environment = Literal["development", "test", "production"]
AiProviderKind = Literal["mock", "openai", "anthropic", "none"]


def _split(v: object) -> object:
    """Comma separated env strings become lists. pydantic-settings would try to JSON-decode
    a ``list[str]`` field first, so these fields are also annotated ``NoDecode``."""
    if isinstance(v, str):
        return [item.strip() for item in v.split(",") if item.strip()]
    return v


def _insecure(value: str) -> bool:
    return value in INSECURE_SECRETS or "not-for-production" in value or len(value) < 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    environment: Environment = "development"
    log_level: str = "INFO"
    cloud: Literal["local", "floci", "aws"] = Field("local", alias="DBPILOT_CLOUD")

    # DBPilot's own database (connections, snapshots, query history, audit log).
    database_url: str = "postgresql+asyncpg://dbpilot:dbpilot@localhost:5842/dbpilot"
    db_pool_size: int = Field(10, ge=1, le=200)
    db_max_overflow: int = Field(20, ge=0, le=400)
    db_pool_timeout_s: float = Field(10.0, gt=0, le=120)
    # Caches, rate limits and short-lived query results. Optional: without it they live in
    # process memory, which is exact for one replica and documented as such.
    redis_url: str = ""
    memory_cache_max_bytes: int = Field(64_000_000, ge=1_000_000)

    session_secret: str = "dev-session-secret-not-for-production"  # noqa: S105 refused in production by guard()
    session_ttl_hours: int = Field(12, ge=1, le=24 * 30)
    cookie_secure: bool = False
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5810", "http://127.0.0.1:5810"]
    )
    # Where people open the dashboard.
    public_url: str = "http://localhost:5810"

    # Connection passwords are sealed before storage: AES-256-GCM with a local key, or
    # AWS KMS (Floci locally) with the record bound into the encryption context.
    key_mode: Literal["local", "kms"] = "local"
    local_master_key: str = "dev-local-master-key-not-for-production"
    kms_key_id: str = "alias/dbpilot-local"

    aws_region: str = "us-east-1"
    aws_endpoint_url: str | None = None
    s3_force_path_style: bool = False
    # Backups and uploaded SQLite files. Rows keep only the object key.
    storage: Literal["local", "s3"] = "local"
    objects_bucket: str = "dbpilot-local-objects"
    local_storage_dir: str = "./.data/objects"
    work_dir: str = "./.data/work"

    # The AI assistant. "mock" is the deterministic built-in model (tests, demo); "openai"
    # speaks the OpenAI chat completions format (and every compatible gateway, including
    # the mock AI service on port 5650); "none" turns model calls off (explanations and
    # diagnoses then come from the syntax tree and rules alone).
    ai_provider: AiProviderKind = "mock"
    ai_base_url: str = ""
    ai_api_key: str = ""
    ai_model: str = "dbpilot-mock"
    ai_timeout_s: float = Field(60.0, gt=0, le=600)
    # The schema part of a prompt stays under this many characters (relevant tables first).
    prompt_schema_chars: int = Field(24_000, ge=2_000, le=400_000)

    # Connected databases usually live on private networks, so private hosts are allowed;
    # link-local and instance metadata addresses never are.
    source_allow_private: bool = True
    max_upload_bytes: int = Field(200_000_000, ge=1_000_000)
    max_body_bytes: int = Field(2_000_000, ge=10_000)

    # Ceilings a connection's own safety limits can never exceed.
    max_rows_ceiling: int = Field(100_000, ge=10)
    max_timeout_ceiling_s: int = Field(600, ge=1, le=3600)

    # Schema discovery and its cache (docs/architecture/HLD.md, caching layers).
    schema_max_tables: int = Field(2_000, ge=10, le=50_000)
    schema_cache_ttl_s: int = Field(3_600, ge=1)
    # A snapshot older than this is rediscovered from the live database on the next read.
    schema_max_age_minutes: int = Field(60, ge=1)
    # Per-connection health card and dashboard aggregates: short response caches.
    health_cache_ttl_s: int = Field(15, ge=0, le=3_600)
    dashboard_cache_ttl_s: int = Field(15, ge=0, le=3_600)
    # A query's result rows are kept this long for paging and export, then dropped.
    result_ttl_s: int = Field(3_600, ge=60, le=86_400)

    # A prepared write, migration or restore must be confirmed within this window.
    confirmation_ttl_s: int = Field(600, ge=30, le=86_400)

    # Backups: a logical dump through the connector layer, never a shell-out.
    backup_max_rows: int = Field(5_000_000, ge=1_000)
    # A backup still "running" after this long belongs to a process that died.
    backup_stale_minutes: int = Field(120, ge=5)

    # The scheduled-backup worker (``dbpilot worker``).
    worker_poll_interval_s: float = Field(5.0, gt=0, le=300)
    worker_batch: int = Field(4, ge=1, le=100)
    worker_metrics_port: int = Field(9464, ge=0, le=65535)

    # Abuse limits on credential routes.
    login_rpm_per_ip: int = Field(20, ge=1)
    connection_test_rpm: int = Field(30, ge=1)

    otel_enabled: bool = False

    # The CLI. ``dbpilot serve`` trusts X-Forwarded-For only from these addresses (the load
    # balancer's network); ``create-admin`` reads the password from DBPILOT_ADMIN_PASSWORD.
    port: int = Field(8080, ge=1, le=65535)
    forwarded_allow_ips: str = "127.0.0.1"
    admin_password: str = Field("", alias="DBPILOT_ADMIN_PASSWORD")

    # ``dbpilot seed``: the demo databases' administrator URLs (the seed builds them) and
    # the passwords of the demo people and of the read-only logins DBPilot uses. Empty
    # source URLs are skipped.
    demo_password: str = Field("Demo-Owner-Pass-1", alias="DBPILOT_DEMO_PASSWORD")
    demo_reader_password: str = Field("Demo-Reader-Pass-1", alias="DBPILOT_DEMO_READER_PASSWORD")
    demo_source_pg_admin: str = Field("", alias="DBPILOT_DEMO_SOURCE_PG_ADMIN")
    demo_source_mysql_admin: str = Field("", alias="DBPILOT_DEMO_SOURCE_MYSQL_ADMIN")
    demo_source_mssql_admin: str = Field("", alias="DBPILOT_DEMO_SOURCE_MSSQL_ADMIN")
    demo_source_mongo_admin: str = Field("", alias="DBPILOT_DEMO_SOURCE_MONGO_ADMIN")
    demo_source_redis_url: str = Field("", alias="DBPILOT_DEMO_SOURCE_REDIS_URL")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        return _split(v)

    @property
    def cache_backend(self) -> Literal["redis", "memory"]:
        return "redis" if self.redis_url else "memory"

    def problems(self) -> list[str]:
        """Every unsafe production option, so an operator fixes them all in one restart."""
        problems: list[str] = []
        if _insecure(self.session_secret):
            problems.append("SESSION_SECRET must be a random value of at least 32 characters")
        if self.key_mode == "local" and _insecure(self.local_master_key):
            problems.append(
                "LOCAL_MASTER_KEY must be a random value of 32+ characters (or KEY_MODE=kms)"
            )
        if self.key_mode == "local" and self.local_master_key == self.session_secret:
            problems.append("LOCAL_MASTER_KEY and SESSION_SECRET must differ")
        if self.cloud == "aws" and self.aws_endpoint_url:
            problems.append("AWS_ENDPOINT_URL must be unset when DBPILOT_CLOUD=aws")
        if not self.cookie_secure:
            problems.append("COOKIE_SECURE must be true in production")
        if self.storage == "local":
            problems.append(
                "STORAGE must be s3 in production (the API and the worker share backups)"
            )
        if self.ai_provider == "mock":
            problems.append(
                "AI_PROVIDER=mock is a deterministic test model: use openai, anthropic or none"
            )
        if self.ai_provider in ("openai", "anthropic") and not self.ai_api_key:
            problems.append("AI_API_KEY is required for the configured AI_PROVIDER")
        if self.ai_provider in ("openai", "anthropic") and self.ai_model in ("", "dbpilot-mock"):
            problems.append("AI_MODEL must name one of the configured provider's models")
        if any(origin.strip() == "*" for origin in self.cors_origins):
            problems.append("CORS_ORIGINS must list origins, never *")
        if not self.public_url.startswith("https://"):
            problems.append("PUBLIC_URL must be https in production")
        return problems

    def guard(self) -> None:
        """Refuse to boot in production with demo secrets or unsafe options."""
        if self.environment != "production":
            return
        if problems := self.problems():
            raise RuntimeError("unsafe production configuration: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.guard()
    return settings

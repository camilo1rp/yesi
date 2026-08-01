"""Application settings, loaded from environment / .env."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AnyUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        case_sensitive=False,
    )

    ENV: Literal["dev", "test", "production"] = "dev"
    LOG_LEVEL: str = "INFO"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@postgres:5432/legalbot"
    DATABASE_URL_SYNC: str = "postgresql+psycopg://postgres:postgres@postgres:5432/legalbot"
    DATABASE_URL_PSYCOPG: str = "postgresql://postgres:postgres@postgres:5432/legalbot"
    DB_POOL_MIN: int = 2
    DB_POOL_MAX: int = 20

    # Redis / Celery
    REDIS_URL: str = "redis://redis:6379/0"
    CELERY_BROKER_URL: str = "redis://redis:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://redis:6379/2"
    CELERY_REDBEAT_URL: str = "redis://redis:6379/3"
    CELERY_REDBEAT_LOCK_KEY: str = "redbeat:lock:legalbot"
    CELERY_CONCURRENCY: int = 4

    # Ingestion & dispatch
    POLL_INTERVAL_SEC: int = 60
    DISPATCH_INTERVAL_SEC: int = 10
    DISPATCH_LEASE_SEC: int = 120
    PROCESSING_LEASE_SEC: int = 1800
    MAX_CONCURRENT_RUNS: int = 10
    INTERRUPT_TTL_SEC: int = 60 * 60 * 72
    ASK_HUMAN_PER_RUN_CAP: int = 8
    ARTIFACT_VERSION_CAP: int = 20
    ARTIFACT_INLINE_MAX_BYTES: int = 32 * 1024

    # Models
    AGENT_MODEL: str = "anthropic:claude-sonnet-4-5"
    SUMMARIZATION_MODEL: str = "anthropic:claude-haiku-4-5"
    VISION_MODEL: str = "anthropic:claude-opus-4-6"
    EMBED_MODEL: str = "openai:text-embedding-3-small"
    EMBED_DIMS: int = 1536

    # Provider credentials
    ANTHROPIC_API_KEY: SecretStr | None = None
    OPENAI_API_KEY: SecretStr | None = None

    GOOGLE_OAUTH_CLIENT_ID: str | None = None
    GOOGLE_OAUTH_CLIENT_SECRET: SecretStr | None = None
    GOOGLE_OAUTH_REDIRECT_URI: str | None = None

    MS_GRAPH_CLIENT_ID: str | None = None
    MS_GRAPH_CLIENT_SECRET: SecretStr | None = None
    MS_GRAPH_TENANT_ID: str | None = None
    MS_GRAPH_REDIRECT_URI: str | None = None

    FERNET_KEY: SecretStr = SecretStr(
        # 32-url-safe-base64 dev fallback; override in production.
        "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="
    )

    # BlobStore
    BLOB_STORE_KIND: Literal["local", "s3"] = "local"
    BLOB_STORE_PATH: str = "/var/lib/legalbot/blobs"
    BLOB_STORE_S3_BUCKET: str | None = None

    # LangSmith / OTel / Prom
    LANGSMITH_API_KEY: SecretStr | None = None
    LANGSMITH_PROJECT: str = "legalbot"
    OTEL_EXPORTER_OTLP_ENDPOINT: AnyUrl | None = None
    OTEL_SERVICE_NAME: str = "legalbot"

    # BigTool
    BIGTOOL_ENABLED: bool = False

    # Knowledge graph (Phase 1 deterministic indexing)
    KG_ENABLED: bool = True
    KG_SEARCH_TOP_K: int = 8

    # Dev-only email fake mailbox for local e2e
    DEV_FAKE_PROVIDER_ENABLED: bool = True


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

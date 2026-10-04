from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "development", "staging", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
LogFormat = Literal["json", "console"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    app_name: str = "personal-intelligence-agent"
    app_version: str = "0.1.0"
    environment: Environment = "local"
    debug: bool = False

    log_level: LogLevel = "INFO"
    log_format: LogFormat = "json"

    # No default: refusing to start beats silently using the wrong database.
    database_url: PostgresDsn

    database_pool_size: int = Field(default=5, ge=1)
    database_max_overflow: int = Field(default=10, ge=0)
    database_pool_timeout_seconds: float = Field(default=10.0, gt=0)
    database_connect_timeout_seconds: float = Field(default=5.0, gt=0)
    database_command_timeout_seconds: float = Field(default=30.0, gt=0)

    # Keep readiness probes fast even when the database is struggling.
    database_health_timeout_seconds: float = Field(default=2.0, gt=0)

    # HS256 needs at least a 32-byte key.
    jwt_secret: SecretStr = Field(min_length=32)
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"

    # Access tokens can't be revoked, so keep them short-lived.
    access_token_ttl_seconds: int = Field(default=15 * 60, gt=0)
    refresh_token_ttl_seconds: int = Field(default=30 * 24 * 60 * 60, gt=0)

    # Optional locally; production requires it (validated below).
    openai_api_key: SecretStr | None = None
    llm_model: str = "gpt-4.1-mini"

    # Extraction should be reproducible; this is not a creative writing task.
    llm_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    llm_timeout_seconds: float = Field(default=30.0, gt=0)
    llm_max_attempts: int = Field(default=2, ge=1, le=5)

    # Vector width is fixed in the schema; changing the model needs a re-embed.
    embedding_model: str = "text-embedding-3-small"
    embedding_timeout_seconds: float = Field(default=15.0, gt=0)

    # Retrieval size and relevance cutoff; more context isn't better context.
    memory_retrieval_limit: int = Field(default=6, ge=1, le=50)
    memory_similarity_threshold: float = Field(default=0.25, ge=0.0, le=1.0)

    # Optional locally; production requires it (validated below).
    redis_url: RedisDsn | None = None

    rate_limit_enabled: bool = True

    # Monthly plan allowances; off only for evaluation and benchmarks.
    usage_limits_enabled: bool = True

    # Extraction costs model calls per exchange; off is a supported mode.
    memory_extraction_enabled: bool = True

    # How often idle workers poll for jobs.
    worker_poll_interval_seconds: float = Field(default=1.0, gt=0)

    # The lease must outlast the timeout so a running job is never claimed twice.
    job_timeout_seconds: float = Field(default=120.0, gt=0)
    job_lease_seconds: float = Field(default=300.0, gt=0)

    # Bounded retries with exponential backoff.
    job_max_attempts: int = Field(default=3, ge=1, le=10)
    job_retry_base_seconds: float = Field(default=30.0, gt=0)

    # How long run records and finished jobs are kept.
    run_retention_days: int = Field(default=30, ge=1)
    job_retention_days: int = Field(default=7, ge=1)

    # Fail open: a Redis outage shouldn't take down login and paid endpoints.
    rate_limit_fail_open: bool = True

    # Rate limiting must never wait on Redis longer than the request can afford.
    rate_limit_timeout_seconds: float = Field(default=0.25, gt=0)

    # X-Forwarded-For is spoofable; trust it only behind a proxy you control.
    trust_proxy_headers: bool = False

    # Far above any real request; bounds what an anonymous caller can send.
    max_request_body_bytes: int = Field(default=1024 * 1024, ge=1024)

    @field_validator("openai_api_key", mode="before")
    @classmethod
    def _blank_key_is_no_key(cls, value: object) -> object:
        # A blank value (as in .env.example) means "not configured".
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _reject_debug_in_production(self) -> "Settings":
        # Debug output must never reach production.
        if self.environment == "production" and self.debug:
            raise ValueError("debug must be disabled when environment is 'production'")
        return self

    @model_validator(mode="after")
    def _require_async_database_driver(self) -> "Settings":
        # Everything is async; a sync driver would block the event loop.
        if self.database_url.scheme != "postgresql+asyncpg":
            raise ValueError("database_url must use the postgresql+asyncpg scheme")
        return self

    @model_validator(mode="after")
    def _require_lease_beyond_job_timeout(self) -> "Settings":
        if self.job_lease_seconds <= self.job_timeout_seconds:
            raise ValueError("job_lease_seconds must exceed job_timeout_seconds")
        return self

    @model_validator(mode="after")
    def _reject_short_lived_refresh_than_access(self) -> "Settings":
        if self.refresh_token_ttl_seconds <= self.access_token_ttl_seconds:
            raise ValueError("refresh_token_ttl_seconds must exceed access_token_ttl_seconds")
        return self

    @model_validator(mode="after")
    def _require_llm_credentials_in_production(self) -> "Settings":
        if self.environment == "production" and self.openai_api_key is None:
            raise ValueError("openai_api_key is required when environment is 'production'")
        return self

    @model_validator(mode="after")
    def _require_rate_limiting_in_production(self) -> "Settings":
        # Production must not start without rate limiting.
        if self.environment != "production":
            return self
        if not self.rate_limit_enabled:
            raise ValueError("rate_limit_enabled must be true when environment is 'production'")
        if self.redis_url is None:
            raise ValueError("redis_url is required when environment is 'production'")
        return self

    @model_validator(mode="after")
    def _require_usage_limits_in_production(self) -> "Settings":
        if self.environment == "production" and not self.usage_limits_enabled:
            raise ValueError("usage_limits_enabled must be true when environment is 'production'")
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

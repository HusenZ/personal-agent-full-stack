from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings

VALID_DATABASE_URL = "postgresql+asyncpg://user:secret@localhost:5433/pia"
VALID_JWT_SECRET = "x" * 48


def build_settings(**overrides: Any) -> Settings:
    required: dict[str, Any] = {
        "database_url": VALID_DATABASE_URL,
        "jwt_secret": VALID_JWT_SECRET,
    }
    return Settings(**(required | overrides))


def test_defaults_are_safe_for_local_development(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    # Run from an empty dir so a local .env doesn't affect defaults.
    monkeypatch.chdir(tmp_path)

    settings = build_settings()

    assert settings.environment == "local"
    assert settings.debug is False
    assert settings.log_format == "json"
    assert settings.is_production is False


def test_production_rejects_debug_mode():
    with pytest.raises(ValidationError, match="debug must be disabled"):
        build_settings(environment="production", debug=True, openai_api_key="sk-not-a-real-key")


def test_production_without_debug_is_accepted():
    settings = build_settings(environment="production", openai_api_key="sk-not-a-real-key")

    assert settings.is_production is True


def test_production_requires_model_credentials():
    """A production deployment that cannot reach a model should not start."""
    with pytest.raises(ValidationError, match="openai_api_key"):
        build_settings(environment="production")


def test_production_requires_usage_limits():
    with pytest.raises(ValidationError, match="usage_limits_enabled"):
        build_settings(
            environment="production",
            openai_api_key="sk-not-a-real-key",
            usage_limits_enabled=False,
        )


def test_model_credentials_are_optional_outside_production():
    assert build_settings(environment="local").openai_api_key is None


def test_model_api_key_is_not_exposed_by_repr():
    settings = build_settings(openai_api_key="sk-super-secret-value")

    assert "sk-super-secret-value" not in repr(settings)


def test_unknown_environment_is_rejected():
    with pytest.raises(ValidationError):
        build_settings(environment="prod")


def test_database_url_is_required(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValidationError, match="database_url"):
        Settings()


def test_synchronous_database_driver_is_rejected():
    with pytest.raises(ValidationError, match="postgresql\\+asyncpg"):
        build_settings(database_url="postgresql://user:secret@localhost:5433/pia")


def test_jwt_secret_is_required(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValidationError, match="jwt_secret"):
        Settings(database_url=VALID_DATABASE_URL)


def test_short_jwt_secret_is_rejected():
    """A key shorter than the HMAC block weakens the signature it produces."""
    with pytest.raises(ValidationError, match="jwt_secret"):
        build_settings(jwt_secret="too-short")


def test_jwt_secret_is_not_exposed_by_repr():
    settings = build_settings()

    assert VALID_JWT_SECRET not in repr(settings)
    assert settings.jwt_secret.get_secret_value() == VALID_JWT_SECRET


def test_refresh_tokens_must_outlive_access_tokens():
    with pytest.raises(ValidationError, match="refresh_token_ttl_seconds"):
        build_settings(access_token_ttl_seconds=3600, refresh_token_ttl_seconds=60)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("database_pool_size", 0),
        ("database_max_overflow", -1),
        ("database_pool_timeout_seconds", 0),
        ("database_health_timeout_seconds", -1),
    ],
)
def test_nonsensical_pool_settings_are_rejected(field: str, value: float):
    with pytest.raises(ValidationError):
        build_settings(**{field: value})


def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATABASE_URL", VALID_DATABASE_URL)
    monkeypatch.setenv("JWT_SECRET", VALID_JWT_SECRET)
    get_settings.cache_clear()

    assert get_settings() is get_settings()

    get_settings.cache_clear()


class TestBlankModelKey:
    @pytest.mark.parametrize("blank", ["", "   "])
    def test_a_blank_key_is_no_key(self, blank: str):
        """As .env.example ships it; read as an empty secret, it passed for configured."""
        assert build_settings(environment="local", openai_api_key=blank).openai_api_key is None

    def test_production_refuses_a_blank_key(self):
        with pytest.raises(ValidationError):
            build_settings(
                environment="production", openai_api_key="", redis_url="redis://localhost:6379/0"
            )

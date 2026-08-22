import pytest

from app.config import AppConfig, ConfigurationError


def test_development_defaults_are_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    config = AppConfig.from_environment()
    assert config.BUSINESS_TIMEZONE == "America/New_York"
    assert config.REVIEW_SMS_DELAY_HOURS == 3
    assert config.GOOGLE_SYNC_ENABLED is False
    assert config.OUTLOOK_SEND_ENABLED is False


def test_production_refuses_weak_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "short")
    monkeypatch.delenv("TOKEN_ENCRYPTION_KEY", raising=False)
    with pytest.raises(ConfigurationError, match="Missing secure configuration"):
        AppConfig.from_environment()


def test_invalid_scheduling_value_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("REVIEW_EMAIL_HOUR", "25")
    with pytest.raises(ConfigurationError, match="REVIEW_EMAIL_HOUR"):
        AppConfig.from_environment()


def test_invalid_feature_flag_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("GOOGLE_SYNC_ENABLED", "sometimes")
    with pytest.raises(ConfigurationError, match="GOOGLE_SYNC_ENABLED"):
        AppConfig.from_environment()

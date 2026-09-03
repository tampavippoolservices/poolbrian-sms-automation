from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from datetime import timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cryptography.fernet import Fernet


class ConfigurationError(RuntimeError):
    pass


def _integer(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}")
    return value


def _boolean(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, str(default)).strip().casefold()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be true or false")


@dataclass(frozen=True, slots=True)
class AppConfig:
    APP_ENV: str
    SECRET_KEY: str
    DATABASE_URL: str
    PUBLIC_BASE_URL: str
    BUSINESS_TIMEZONE: str
    LOG_LEVEL: str
    ADMIN_AUTH_MODE: str
    DASHBOARD_USERNAME: str
    DASHBOARD_PASSWORD: str
    ADMIN_ALLOWED_EMAILS: str
    ADMIN_ALLOWED_DOMAIN: str
    TOKEN_ENCRYPTION_KEY: str
    REVIEW_SMS_DELAY_HOURS: int
    REVIEW_EMAIL_HOUR: int
    REVIEW_SUPPRESSION_DAYS: int
    MESSAGE_MAX_ATTEMPTS: int
    MESSAGE_LEASE_MINUTES: int
    GOOGLE_SYNC_ENABLED: bool
    OUTLOOK_SEND_ENABLED: bool
    OUTLOOK_BOUNCE_SYNC_ENABLED: bool
    POOLBRAIN_WEBSITE_LEAD_SYNC_ENABLED: bool

    @classmethod
    def from_environment(cls) -> AppConfig:
        app_env = os.getenv("APP_ENV", "development").strip().lower()
        config = cls(
            APP_ENV=app_env,
            SECRET_KEY=os.getenv("SECRET_KEY", "development-only-secret-change-me"),
            DATABASE_URL=os.getenv(
                "DATABASE_URL",
                "postgresql+psycopg://localhost/tampa_vip_automation",
            ),
            PUBLIC_BASE_URL=os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/"),
            BUSINESS_TIMEZONE=os.getenv("BUSINESS_TIMEZONE", "America/New_York"),
            LOG_LEVEL=os.getenv("LOG_LEVEL", "INFO").upper(),
            ADMIN_AUTH_MODE=os.getenv("ADMIN_AUTH_MODE", "basic").lower(),
            DASHBOARD_USERNAME=os.getenv("DASHBOARD_USERNAME", ""),
            DASHBOARD_PASSWORD=os.getenv("DASHBOARD_PASSWORD", ""),
            ADMIN_ALLOWED_EMAILS=os.getenv("ADMIN_ALLOWED_EMAILS", ""),
            ADMIN_ALLOWED_DOMAIN=os.getenv("ADMIN_ALLOWED_DOMAIN", ""),
            TOKEN_ENCRYPTION_KEY=os.getenv("TOKEN_ENCRYPTION_KEY", ""),
            REVIEW_SMS_DELAY_HOURS=_integer("REVIEW_SMS_DELAY_HOURS", 3, 0, 72),
            REVIEW_EMAIL_HOUR=_integer("REVIEW_EMAIL_HOUR", 10, 0, 23),
            REVIEW_SUPPRESSION_DAYS=_integer("REVIEW_SUPPRESSION_DAYS", 120, 1, 3650),
            MESSAGE_MAX_ATTEMPTS=_integer("MESSAGE_MAX_ATTEMPTS", 5, 1, 20),
            MESSAGE_LEASE_MINUTES=_integer("MESSAGE_LEASE_MINUTES", 10, 1, 120),
            GOOGLE_SYNC_ENABLED=_boolean("GOOGLE_SYNC_ENABLED"),
            OUTLOOK_SEND_ENABLED=_boolean("OUTLOOK_SEND_ENABLED"),
            OUTLOOK_BOUNCE_SYNC_ENABLED=_boolean("OUTLOOK_BOUNCE_SYNC_ENABLED"),
            POOLBRAIN_WEBSITE_LEAD_SYNC_ENABLED=_boolean("POOLBRAIN_WEBSITE_LEAD_SYNC_ENABLED"),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if self.APP_ENV not in {"development", "test", "staging", "production"}:
            raise ConfigurationError("APP_ENV is invalid")
        if self.ADMIN_AUTH_MODE not in {"basic", "oidc"}:
            raise ConfigurationError("ADMIN_AUTH_MODE must be basic or oidc")
        try:
            ZoneInfo(self.BUSINESS_TIMEZONE)
        except ZoneInfoNotFoundError as exc:
            raise ConfigurationError("BUSINESS_TIMEZONE is invalid") from exc
        parsed = urlparse(self.PUBLIC_BASE_URL)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ConfigurationError("PUBLIC_BASE_URL must be an absolute HTTP(S) URL")
        if not self.DATABASE_URL.startswith(
            ("postgres://", "postgresql://", "postgresql+psycopg://")
        ):
            raise ConfigurationError("DATABASE_URL must be a PostgreSQL URL")
        if self.TOKEN_ENCRYPTION_KEY:
            try:
                Fernet(self.TOKEN_ENCRYPTION_KEY.encode("ascii"))
            except (TypeError, ValueError) as exc:
                raise ConfigurationError("TOKEN_ENCRYPTION_KEY must be a valid Fernet key") from exc
        if self.APP_ENV in {"staging", "production"}:
            missing = []
            if parsed.scheme != "https":
                missing.append("HTTPS PUBLIC_BASE_URL")
            if not os.getenv("DATABASE_URL"):
                missing.append("DATABASE_URL")
            if len(self.SECRET_KEY) < 32:
                missing.append("SECRET_KEY (minimum 32 characters)")
            if not self.TOKEN_ENCRYPTION_KEY:
                missing.append("TOKEN_ENCRYPTION_KEY")
            if self.ADMIN_AUTH_MODE == "basic" and (
                not self.DASHBOARD_USERNAME or len(self.DASHBOARD_PASSWORD) < 16
            ):
                missing.append("strong DASHBOARD_USERNAME/DASHBOARD_PASSWORD")
            if self.ADMIN_AUTH_MODE == "oidc" and not (
                self.ADMIN_ALLOWED_EMAILS or self.ADMIN_ALLOWED_DOMAIN
            ):
                missing.append("ADMIN_ALLOWED_EMAILS or ADMIN_ALLOWED_DOMAIN")
            required_provider_values = (
                "POOLBRAIN_API_KEY",
                "POOLBRAIN_WEBHOOK_SIGNING_SECRET",
                "TWILIO_ACCOUNT_SID",
                "TWILIO_AUTH_TOKEN",
                "TWILIO_PHONE_NUMBER",
                "GOOGLE_REVIEW_URL",
            )
            missing.extend(name for name in required_provider_values if not os.getenv(name))
            if self.ADMIN_AUTH_MODE == "oidc":
                required_admin_values = (
                    "MICROSOFT_CLIENT_ID",
                    "MICROSOFT_CLIENT_SECRET",
                    "MICROSOFT_ADMIN_REDIRECT_URI",
                )
                missing.extend(name for name in required_admin_values if not os.getenv(name))
            if missing:
                raise ConfigurationError("Missing secure configuration: " + ", ".join(missing))

    def as_flask_config(self) -> dict[str, object]:
        values = asdict(self)
        values.update(
            SESSION_COOKIE_SECURE=self.APP_ENV in {"staging", "production"},
            SESSION_COOKIE_HTTPONLY=True,
            SESSION_COOKIE_SAMESITE="Lax",
            PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
            SESSION_REFRESH_EACH_REQUEST=False,
            MAX_CONTENT_LENGTH=1_048_576,
        )
        return values

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime

_PHONE_PATTERN = re.compile(
    r"(?<!\d)(?:\+?1[ .-]?)?(?:\(\d{3}\)|\d{3})[ .-]?\d{3}[ .-]?\d{4}(?!\d)"
)
_EMAIL_PATTERN = re.compile(r"\b([A-Z0-9._%+-])[A-Z0-9._%+-]*@([A-Z0-9.-]+\.[A-Z]{2,})\b", re.I)
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Z0-9._~+/=-]+")
_SECRET_PATTERN = re.compile(
    r"(?i)\b(access_token|refresh_token|client_secret|authorization)"
    r"([\"']?\s*[:=]\s*[\"']?)([^\"'\s,}]+)"
)


def sanitize_log_text(value: str) -> str:
    value = _PHONE_PATTERN.sub(_mask_phone_match, value)
    value = _EMAIL_PATTERN.sub(lambda match: f"{match.group(1)}***@{match.group(2)}", value)
    value = _BEARER_PATTERN.sub("Bearer [REDACTED]", value)
    return _SECRET_PATTERN.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", value)


def _mask_phone_match(match: re.Match[str]) -> str:
    digits = "".join(character for character in match.group() if character.isdigit())
    return f"***-***-{digits[-4:]}"


def mask_phone(value: str | None) -> str | None:
    if not value:
        return value
    digits = "".join(character for character in value if character.isdigit())
    return f"***-***-{digits[-4:]}" if len(digits) >= 4 else "***"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = sanitize_log_text(record.getMessage())
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
        }
        for key in (
            "event",
            "provider",
            "external_id",
            "job_id",
            "campaign_id",
            "method",
            "route",
            "status_code",
            "request_id",
        ):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = sanitize_log_text(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

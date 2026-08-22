from __future__ import annotations

from datetime import timedelta

TERMINAL_JOB_STATUSES = frozenset({"delivered", "failed", "cancelled", "dead"})

TWILIO_STATUS_RANK = {
    "accepted": 10,
    "scheduled": 15,
    "queued": 20,
    "sending": 30,
    "sent": 40,
    "delivered": 100,
    "undelivered": 100,
    "failed": 100,
    "canceled": 100,
}


def provider_status_rank(status: str | None) -> int:
    return TWILIO_STATUS_RANK.get((status or "").lower(), 0)


def retry_delay(attempt_count: int) -> timedelta:
    seconds = min(3600, 30 * (2 ** max(0, attempt_count - 1)))
    return timedelta(seconds=seconds)


def is_retryable_http_status(status_code: int) -> bool:
    return status_code in {408, 409, 425, 429} or status_code >= 500
